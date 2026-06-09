"""Honest 6174 Stage 0/1 runner: data cache + cheap screening.

This runner is intentionally limited to data availability and cheap data/liquidity
screening. It never runs viability backtests, rolling validation, stock_score, or
any promoted rulebook path.

Output root default:
    data/_system/research/honest_full_6174_20260610/
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.adapters.factory import get_adapter
from engine.learning.learner import _detect_sector_name
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.pipeline.context import (
    DEFAULT_HISTORY_YEARS,
    DEFAULT_MARKET_HISTORY_YEARS,
    attach_sell_omen_scores,
    calculate_adv_usd_252d,
    make_year_splits,
)
from engine.pipeline.screening import run_screening
from engine.strategies.rulebook import default_rulebook
from scripts.research.honest_run_notifications import HonestRunNotifier

DEFAULT_OUTPUT_ROOT = Path("data/_system/research/honest_full_6174_20260610")
TERMINAL_STATUSES = {"DONE", "FAILED", "ERROR"}
CACHE_FORMAT = "pkl"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ticker(ticker: str) -> str:
    return str(ticker or "").upper().strip()


def load_tickers(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [normalize_ticker(x) for x in data if normalize_ticker(x)]
        raise ValueError(f"unsupported json ticker file: {path}")
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        ticker = normalize_ticker(line.split(",")[0].strip())
        if ticker and not ticker.startswith("#"):
            out.append(ticker)
    return out


def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def acquire_parent_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(f"another parent process holds lock: {lock_path}") from exc
    fh.write(f"pid={os.getpid()} acquired_at={utc_now()}\n")
    fh.flush()
    return fh


def cache_paths(cache_dir: Path, ticker: str) -> tuple[Path, Path]:
    ticker = normalize_ticker(ticker)
    return cache_dir / f"{ticker}.{CACHE_FORMAT}", cache_dir / f"{ticker}.meta.json"


def _date_bounds(df: pd.DataFrame) -> tuple[str | None, str | None]:
    if df is None or len(df) == 0:
        return None, None
    if isinstance(df.index, pd.DatetimeIndex):
        dates = pd.to_datetime(df.index, errors="coerce")
    elif "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
    elif "Date" in df.columns:
        dates = pd.to_datetime(df["Date"], errors="coerce")
    else:
        dates = pd.to_datetime(pd.Series(df.index), errors="coerce")
    dates = pd.Series(dates).dropna()
    if dates.empty:
        return None, None
    return pd.Timestamp(dates.min()).strftime("%Y-%m-%d"), pd.Timestamp(dates.max()).strftime("%Y-%m-%d")


def _valid_ratio(series: pd.Series | None, *, positive: bool = False, non_negative: bool = False) -> float:
    if series is None or len(series) == 0:
        return 0.0
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.notna()
    if positive:
        valid = valid & (numeric > 0)
    if non_negative:
        valid = valid & (numeric >= 0)
    return float(valid.sum() / len(numeric)) if len(numeric) else 0.0


def _invalid_price_volume_ratio(df: pd.DataFrame) -> float:
    if df is None or len(df) == 0 or "Close" not in df.columns or "Volume" not in df.columns:
        return 1.0
    close = pd.to_numeric(df["Close"], errors="coerce")
    volume = pd.to_numeric(df["Volume"], errors="coerce")
    invalid = close.isna() | volume.isna() | (close <= 0) | (volume <= 0)
    return float(invalid.sum() / len(df)) if len(df) else 1.0


def context_from_cached_df(ticker: str, df: pd.DataFrame) -> dict[str, Any]:
    ticker = normalize_ticker(ticker)
    adapter = get_adapter(ticker)
    meta = adapter.meta
    data_start, data_end = _date_bounds(df)
    market_history_df = get_market_history(years=DEFAULT_MARKET_HISTORY_YEARS)
    ticker_sentiment = load_ticker_sentiment(ticker)
    sector_name = _detect_sector_name(meta.name)
    base_rulebook = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rulebook.sector_name = sector_name
    close_series = df["Close"] if df is not None and "Close" in df.columns else None
    volume_series = df["Volume"] if df is not None and "Volume" in df.columns else None
    return {
        "ticker": ticker,
        "adapter": adapter,
        "meta": meta,
        "df": df,
        "rows": int(len(df) if df is not None else 0),
        "data_min": data_start,
        "data_max": data_end,
        "data_start": data_start,
        "data_end": data_end,
        "valid_close_ratio": _valid_ratio(close_series, positive=True),
        "valid_volume_ratio": _valid_ratio(volume_series, non_negative=True),
        "invalid_price_volume_ratio": _invalid_price_volume_ratio(df),
        "splits": make_year_splits(data_min=data_start, data_max=data_end),
        "split_count": len(make_year_splits(data_min=data_start, data_max=data_end)),
        "market_history_df": market_history_df,
        "ticker_sentiment": ticker_sentiment,
        "sentiment_days": len(ticker_sentiment or {}),
        "sector_name": sector_name,
        "base_rulebook": base_rulebook,
        "adv_usd_252d": calculate_adv_usd_252d(df),
        "sell_omen_score": {"available": "cached_df"},
    }


def load_or_download_context(ticker: str, cache_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    ticker = normalize_ticker(ticker)
    df_path, meta_path = cache_paths(cache_dir, ticker)
    cache_info: dict[str, Any] = {
        "cache_path": str(df_path),
        "meta_path": str(meta_path),
        "cache_format": CACHE_FORMAT,
        "cache_hit": False,
        "downloaded": False,
    }
    if df_path.exists():
        df = pd.read_pickle(df_path)
        ctx = context_from_cached_df(ticker, df)
        cache_info["cache_hit"] = True
        return ctx, cache_info

    adapter = get_adapter(ticker)
    df = adapter.load_history(years=DEFAULT_HISTORY_YEARS)
    df, sell_omen_info = attach_sell_omen_scores(df, ticker)
    df_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(df_path)
    ctx = context_from_cached_df(ticker, df)
    ctx["sell_omen_score"] = sell_omen_info
    meta = {
        "ticker": ticker,
        "created_at": utc_now(),
        "rows": ctx.get("rows"),
        "data_start": ctx.get("data_start"),
        "data_end": ctx.get("data_end"),
        "adv_usd_252d": ctx.get("adv_usd_252d"),
        "cache_format": CACHE_FORMAT,
        "source": "adapter.load_history(years=6)",
        "sell_omen_score": sell_omen_info,
    }
    atomic_json_write(meta_path, meta)
    cache_info["downloaded"] = True
    return ctx, cache_info


def worker_screen_ticker(ticker: str, output_root: str, run_viability: bool) -> dict[str, Any]:
    started = time.time()
    ticker = normalize_ticker(ticker)
    root = Path(output_root)
    cache_dir = root / "stage0" / "ohlcv_cache"
    row: dict[str, Any] = {
        "ticker": ticker,
        "started_at": utc_now(),
        "run_viability": bool(run_viability),
    }
    try:
        ctx, cache_info = load_or_download_context(ticker, cache_dir)
        screening = run_screening(ticker, context=ctx, run_viability=run_viability, include_context=False)
        row.update(
            {
                "status": "DONE" if screening.get("status") in {"PASS", "FAIL"} else str(screening.get("status") or "DONE"),
                "load_success": True,
                "rows": ctx.get("rows"),
                "data_start": ctx.get("data_start"),
                "data_end": ctx.get("data_end"),
                "adv_usd_252d": ctx.get("adv_usd_252d"),
                "split_count": ctx.get("split_count"),
                "valid_close_ratio": ctx.get("valid_close_ratio"),
                "valid_volume_ratio": ctx.get("valid_volume_ratio"),
                "invalid_price_volume_ratio": ctx.get("invalid_price_volume_ratio"),
                "screening_passed": bool(screening.get("passed")),
                "screening_status": screening.get("status"),
                "fail_reason": screening.get("reason_code") or "",
                "viability_executed": bool(((screening.get("viability") or {}).get("executed"))),
                "cache": cache_info,
                "screening": {k: v for k, v in screening.items() if k != "_context"},
            }
        )
    except Exception as exc:
        row.update(
            {
                "status": "ERROR",
                "load_success": False,
                "screening_passed": False,
                "fail_reason": "ERROR",
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
            }
        )
    row["finished_at"] = utc_now()
    row["elapsed_sec"] = time.time() - started
    return row


def load_progress(progress_path: Path, tickers: list[str]) -> dict[str, dict[str, Any]]:
    if progress_path.exists():
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {normalize_ticker(k): dict(v) for k, v in data.items() if normalize_ticker(k)}
        except Exception:
            pass
    return {ticker: {"status": "PENDING"} for ticker in tickers}


def save_progress(progress_path: Path, progress: dict[str, dict[str, Any]]) -> None:
    atomic_json_write(progress_path, progress)


def build_summary(rows: list[dict[str, Any]], progress: dict[str, dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    total = len(progress)
    terminal = sum(1 for item in progress.values() if item.get("status") in TERMINAL_STATUSES)
    passed = [row["ticker"] for row in rows if row.get("screening_passed")]
    failed = [row for row in rows if not row.get("screening_passed")]
    status_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for row in rows:
        status_counts[str(row.get("status") or "UNKNOWN")] = status_counts.get(str(row.get("status") or "UNKNOWN"), 0) + 1
        reason = str(row.get("fail_reason") or "PASS") if not row.get("screening_passed") else "PASS"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "run_id": args.run_id,
        "created_at": utc_now(),
        "output_root": str(args.output_root),
        "input_ticker_source": str(args.tickers),
        "max_workers": args.max_workers,
        "run_viability": bool(args.run_viability),
        "cache_format": CACHE_FORMAT,
        "counts": {
            "input_tickers": total,
            "terminal_progress": terminal,
            "rows_written": len(rows),
            "screening_passed": len(passed),
            "screening_failed_or_error": len(failed),
        },
        "status_counts": status_counts,
        "fail_reason_counts": reason_counts,
        "honesty_flags": {
            "stock_score_gate_used": False,
            "stock_score_cutoff_used": False,
            "rolling_oos_score_used": False,
            "viability_backtest_used": bool(args.run_viability),
            "promoted_rulebook_used": False,
            "parameters_json_rulebook_used": False,
            "load_live_universe_used": False,
            "cheap_filter_only": not bool(args.run_viability),
        },
        "outputs": {
            "stage0_screening": str(Path(args.output_root) / "stage0" / "stage0_screening.jsonl"),
            "stage0_failures": str(Path(args.output_root) / "stage0" / "stage0_failures.jsonl"),
            "stage1_pass_tickers": str(Path(args.output_root) / "stage1" / "stage1_pass_tickers.txt"),
            "progress": str(Path(args.output_root) / "stage0" / "stage0_progress.json"),
            "ohlcv_cache": str(Path(args.output_root) / "stage0" / "ohlcv_cache"),
        },
        "passed_ready_for_stage2": terminal == total and not bool(args.run_viability),
    }


def honesty_flags_ok(summary: dict[str, Any]) -> bool:
    flags = summary.get("honesty_flags") or {}
    return (
        flags.get("stock_score_gate_used") is False
        and flags.get("stock_score_cutoff_used") is False
        and flags.get("rolling_oos_score_used") is False
        and flags.get("viability_backtest_used") is False
        and flags.get("promoted_rulebook_used") is False
        and flags.get("parameters_json_rulebook_used") is False
        and flags.get("load_live_universe_used") is False
        and flags.get("cheap_filter_only") is True
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", type=Path, default=Path("data/_system/screening_universe_all.txt"))
    parser.add_argument("--run-id", default="honest6174_20260610")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--run-viability", default="false")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--limit", type=int, default=0, help="optional smoke limit from the input ticker file")
    parser.add_argument("--notify-every", type=int, default=100, help="telegram progress interval by completed ticker count")
    parser.add_argument("--notify-pct", type=float, default=5.0, help="telegram progress interval by percent bucket")
    args = parser.parse_args()
    args.run_viability = str(args.run_viability).strip().lower() in {"1", "true", "yes", "y"}
    return args


def main() -> int:
    args = parse_args()
    root = Path(args.output_root)
    stage0 = root / "stage0"
    stage1 = root / "stage1"
    stage0.mkdir(parents=True, exist_ok=True)
    stage1.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    notifier: HonestRunNotifier | None = None
    lock_fh = acquire_parent_lock(stage0 / ".stage0_parent.lock")
    try:
        tickers = load_tickers(args.tickers)
        if args.limit and args.limit > 0:
            tickers = tickers[: args.limit]
        tickers = list(dict.fromkeys(tickers))
        notifier = HonestRunNotifier(
            run_id=args.run_id,
            stage="stage0_screening",
            batch_index="stage0",
            total=len(tickers),
            notify_every=args.notify_every,
            notify_pct=args.notify_pct,
        )
        notifier.start(
            total=len(tickers),
            batch_index="stage0",
            extra={
                "max_workers": args.max_workers,
                "run_viability": bool(args.run_viability),
                "output_root": str(root),
            },
        )
        progress_path = stage0 / "stage0_progress.json"
        progress = load_progress(progress_path, tickers)
        for ticker in tickers:
            progress.setdefault(ticker, {"status": "PENDING"})
        pending = [ticker for ticker in tickers if progress.get(ticker, {}).get("status") not in TERMINAL_STATUSES]
        for ticker in pending:
            progress[ticker] = {"status": "RUNNING", "claimed_at": utc_now(), "run_id": args.run_id}
        save_progress(progress_path, progress)

        screening_path = stage0 / "stage0_screening.jsonl"
        failures_path = stage0 / "stage0_failures.jsonl"
        existing_rows: list[dict[str, Any]] = []
        if screening_path.exists():
            for line in screening_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        existing_rows.append(json.loads(line))
                    except Exception:
                        pass

        completed_count = len(tickers) - len(pending)
        passed_count = sum(1 for row in existing_rows if row.get("screening_passed"))
        error_count = sum(1 for item in progress.values() if item.get("status") == "ERROR")
        if notifier and completed_count:
            notifier.progress(done=completed_count, passed=passed_count, errors=error_count, force=True)

        with ProcessPoolExecutor(max_workers=max(1, int(args.max_workers))) as executor:
            futures = {
                executor.submit(worker_screen_ticker, ticker, str(root), bool(args.run_viability)): ticker
                for ticker in pending
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "ticker": ticker,
                        "status": "ERROR",
                        "load_success": False,
                        "screening_passed": False,
                        "fail_reason": "WORKER_EXCEPTION",
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                        "finished_at": utc_now(),
                    }
                append_jsonl(screening_path, row)
                if not row.get("screening_passed"):
                    append_jsonl(failures_path, row)
                progress[ticker] = {
                    "status": "DONE" if row.get("status") != "ERROR" else "ERROR",
                    "screening_passed": bool(row.get("screening_passed")),
                    "fail_reason": row.get("fail_reason") or "",
                    "finished_at": row.get("finished_at") or utc_now(),
                }
                save_progress(progress_path, progress)
                existing_rows.append(row)
                completed_count += 1
                if row.get("screening_passed"):
                    passed_count += 1
                if row.get("status") == "ERROR":
                    error_count += 1
                    if notifier:
                        notifier.error(ticker=ticker, error=(row.get("error") or {}).get("message") or row.get("fail_reason"), context="stage0_worker")
                if notifier:
                    notifier.progress(done=completed_count, passed=passed_count, errors=error_count)

        latest_by_ticker: dict[str, dict[str, Any]] = {}
        for row in existing_rows:
            ticker = normalize_ticker(row.get("ticker"))
            if ticker:
                latest_by_ticker[ticker] = row
        pass_tickers = [ticker for ticker in tickers if latest_by_ticker.get(ticker, {}).get("screening_passed")]
        (stage1 / "stage1_pass_tickers.txt").write_text("\n".join(pass_tickers) + ("\n" if pass_tickers else ""), encoding="utf-8")
        summary = build_summary(list(latest_by_ticker.values()), progress, args)
        atomic_json_write(stage0 / "stage0_summary.json", summary)
        if notifier:
            notifier.complete(
                total=summary["counts"]["input_tickers"],
                passed=summary["counts"]["screening_passed"],
                errors=summary["status_counts"].get("ERROR", 0),
                elapsed_sec=time.time() - started_at,
                honesty_ok=honesty_flags_ok(summary),
                extra={
                    "ready_for_stage2": summary.get("passed_ready_for_stage2"),
                    "cache_format": summary.get("cache_format"),
                },
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0 if summary["counts"]["terminal_progress"] == summary["counts"]["input_tickers"] else 1
    except Exception as exc:
        if notifier:
            notifier.error(ticker="-", error=f"{type(exc).__name__}: {exc}", context="stage0_main_crash")
        raise
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            lock_fh.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
