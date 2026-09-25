#!/usr/bin/env python3
"""LR8D 6174 다중개체 robust shard launcher.

이 파일은 scripts/research/run_lr8d_multientity_6174.py 의 실제 장시간 실행용
런처입니다.

무엇을 하는 파일인가:
- 6174개 전체 ticker를 shard로 나눠 백그라운드에서 처리한다.
- 기존 LR8D A+B+C+D의 run_one_period/GA/Top-N 저장 로직을 그대로 쓴다.
- 한 ticker에서 데이터 오류, 상폐 티커 오류, 결측 오류가 나도 shard 전체가 죽지 않는다.
- 실패 ticker/period는 lr8d_multi6174_failures.jsonl에 이유와 함께 기록하고 다음 작업으로 넘어간다.
- 최종 산출물은 종목당 1개 제한이 아니라, strict_k3 통과 ticker 안에서 중복 진입전략을 제거한 다중개체 후보다.
- LR8D_MULTI_EARLY_PRUNE=1이면 한 구간이라도 통과 기준에 실패한 ticker의 남은 구간은 즉시 스킵한다.

주의:
- 이 파일은 research artifact만 생성한다.
- live parameters 또는 data/symbols/parameters.json을 수정하지 않는다.
- live trading runner를 시작/중지하지 않는다.
- shard 실행은 TOPN/RULEBOOKS/TRADES append만 수행하고, 최종 survivor/multi/report는 기본적으로 쓰지 않는다.
- 모든 shard가 끝난 뒤 같은 명령에 --finalize-only를 붙여 단일 aggregation을 1회 실행한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research import run_lr8d_multientity_6174 as base

runner = base.runner
FAILURES_PATH = base.OUT_DIR / f"{base.RUN_PREFIX}_failures.jsonl"
PROGRESS_PATH = base.OUT_DIR / f"{base.RUN_PREFIX}_progress.json"
PRUNED_PATH = base.OUT_DIR / f"{base.RUN_PREFIX}_pruned_tickers.jsonl"
FINALIZE_LOCK_PATH = base.OUT_DIR / f"{base.RUN_PREFIX}_finalize.lock"
EARLY_PRUNE_ENABLED = os.environ.get("LR8D_MULTI_EARLY_PRUNE", "0").strip().lower() in {"1", "true", "yes", "on"}
EARLY_PRUNE_MIN_EXPECTANCY_PCT = float(os.environ.get("LR8D_MULTI_EARLY_PRUNE_MIN_EXPECTANCY_PCT", str(base.MIN_ENTITY_EXPECTANCY_PCT)))
EARLY_PRUNE_DD_CUTOFF = float(os.environ.get("LR8D_MULTI_EARLY_PRUNE_DD_CUTOFF", str(base.DD_CUTOFF)))
FINALIZE_ON_SHARD_COMPLETE = os.environ.get("LR8D_MULTI_FINALIZE_ON_SHARD_COMPLETE", "0").strip().lower() in {"1", "true", "yes", "on"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LR8D multi-entity robust shard/finalize launcher")
    parser.add_argument("--stop-after-step0", action="store_true")
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument(
        "--finalize-only",
        action="store_true",
        help="모든 shard 완료 후 survivor/multi/report를 단일 프로세스에서 1회 생성",
    )
    parser.add_argument(
        "--finalize-on-shard-complete",
        action="store_true",
        help="호환용 옵션. 기본은 off이며 race 방지를 위해 완료 후 --finalize-only 사용 권장",
    )
    return parser.parse_args()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _failure_row(*, ticker: str, split_label: str, stage: str, exc: BaseException, shard_count: int, shard_index: int) -> dict[str, Any]:
    return {
        "_comment": "LR8D 6174 다중개체 robust 실행 중 실패한 ticker/period 기록입니다. 이 파일은 실패로 shard가 죽지 않도록 남기는 감사 로그입니다.",
        "run_id": base.RUN_ID,
        "ticker": ticker,
        "split_label": split_label,
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback_tail": traceback.format_exc(limit=8),
        "created_at": runner.utc_now(),
        "shard_count": int(shard_count),
        "shard_index": int(shard_index),
    }


def _pruned_row(*, ticker: str, split_label: str, row: Mapping[str, Any] | None, reason: str, shard_count: int, shard_index: int) -> dict[str, Any]:
    best = _best_period_candidate(row or {})
    best_metrics = best.get("oos_metrics") if isinstance(best.get("oos_metrics"), Mapping) else best.get("oos") if isinstance(best.get("oos"), Mapping) else {}
    return {
        "_comment": "LR8D 다중개체 early-prune 기록입니다. 한 구간이라도 통과 기준에 실패한 ticker는 남은 구간을 돌리지 않고 버립니다.",
        "run_id": base.RUN_ID,
        "ticker": ticker,
        "split_label": split_label,
        "reason": reason,
        "period_gate": {
            "min_trades": int(runner.MIN_TRADES),
            "min_member_score": float(runner.MIN_MEMBER_SCORE),
            "min_expectancy_pct": float(EARLY_PRUNE_MIN_EXPECTANCY_PCT),
            "max_drawdown_pct_gt": float(EARLY_PRUNE_DD_CUTOFF),
            "required": "at_least_one_candidate_passes_every_period",
        },
        "period_summary": {
            "qualified_count": (row or {}).get("qualified_count"),
            "candidate_pool_count": (row or {}).get("candidate_pool_count"),
            "best_rulebook_hash": best.get("rulebook_hash"),
            "best_oos_member_score": best.get("oos_member_score"),
            "best_expectancy_pct": _safe_float(best_metrics.get("expectancy_pct"), 0.0),
            "best_trade_count": _safe_int(best_metrics.get("trade_count"), 0),
            "best_max_drawdown_pct": _safe_float(best_metrics.get("max_drawdown_pct"), 0.0),
            "best_profit_factor": _safe_float(best_metrics.get("profit_factor"), 0.0),
            "best_win_rate": _safe_float(best_metrics.get("win_rate"), 0.0),
        },
        "created_at": runner.utc_now(),
        "shard_count": int(shard_count),
        "shard_index": int(shard_index),
    }


def _write_progress(payload: dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": "LR8D 다중개체 robust 실행의 shard-local 최신 상태입니다. 여러 shard가 덮어쓸 수 있으므로 전체 aggregate 진행률은 TOPN/PRUNED/FAILURES JSONL 카운트로 계산하세요.",
        **payload,
    }
    tmp = PROGRESS_PATH.with_suffix(PROGRESS_PATH.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


def _touch_outputs() -> None:
    base.OUT_DIR.mkdir(parents=True, exist_ok=True)
    base.write_readme()
    for path in [runner.TOPN_PATH, runner.RULEBOOKS_PATH, runner.TRADES_PATH, runner.SURVIVORS_PATH, FAILURES_PATH, PRUNED_PATH]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


def _append_failure(row: dict[str, Any]) -> None:
    runner.append_jsonl(FAILURES_PATH, row)


def _append_pruned(row: dict[str, Any]) -> None:
    runner.append_jsonl(PRUNED_PATH, row)


def _candidate_metrics(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(candidate.get("oos_metrics"), Mapping):
        return candidate.get("oos_metrics")  # type: ignore[return-value]
    if isinstance(candidate.get("oos"), Mapping):
        return candidate.get("oos")  # type: ignore[return-value]
    return candidate


def _candidate_passes_period_gate(candidate: Mapping[str, Any]) -> bool:
    metrics = _candidate_metrics(candidate)
    return (
        _safe_int(metrics.get("trade_count"), 0) >= int(runner.MIN_TRADES)
        and _safe_float(candidate.get("oos_member_score"), 0.0) >= float(runner.MIN_MEMBER_SCORE)
        and _safe_float(metrics.get("expectancy_pct"), 0.0) >= float(EARLY_PRUNE_MIN_EXPECTANCY_PCT)
        and _safe_float(metrics.get("max_drawdown_pct"), 0.0) > float(EARLY_PRUNE_DD_CUTOFF)
    )


def _period_row_passes_gate(row: Mapping[str, Any]) -> bool:
    candidates = row.get("candidates") or []
    if not isinstance(candidates, list):
        return False
    return any(isinstance(candidate, Mapping) and _candidate_passes_period_gate(candidate) for candidate in candidates)


def _best_period_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    candidates = [candidate for candidate in (row.get("candidates") or []) if isinstance(candidate, Mapping)]
    if not candidates:
        return {}
    return dict(
        max(
            candidates,
            key=lambda candidate: (
                _safe_float(_candidate_metrics(candidate).get("expectancy_pct"), -999.0),
                _safe_float(_candidate_metrics(candidate).get("profit_factor"), -999.0),
                _safe_float(candidate.get("oos_member_score"), -999.0),
                _safe_float(_candidate_metrics(candidate).get("max_drawdown_pct"), -999.0),
            ),
        )
    )


def _load_existing_rows_for_ticker(ticker: str) -> dict[str, dict[str, Any]]:
    """Load already-written TOPN rows for a ticker by parsing JSON exactly.

    Do not use a short substring prefilter: TOPN JSONL rows are sorted by key and
    the ticker field can appear far beyond the first 200 characters.  A substring
    prefilter caused restart-time early-prune state to be missed.
    """
    rows: dict[str, dict[str, Any]] = {}
    if not runner.TOPN_PATH.exists():
        return rows
    target = ticker.upper().strip()
    with runner.TOPN_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("ticker") or "").upper().strip() != target:
                continue
            label = str(row.get("label") or "")
            if label:
                rows[label] = row
    return rows


def _already_pruned_tickers() -> set[str]:
    out: set[str] = set()
    if not PRUNED_PATH.exists():
        return out
    with PRUNED_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ticker = str(row.get("ticker") or "").upper().strip()
            if ticker:
                out.add(ticker)
    return out


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _load_timing() -> dict[str, Any] | None:
    if runner.TIMING_PATH.exists() and runner.TIMING_PATH.read_text(encoding="utf-8").strip():
        try:
            return json.loads(runner.TIMING_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _finalize_outputs(all_symbols: tuple[str, ...], timing: dict[str, Any] | None, *, args: argparse.Namespace) -> int:
    """Write survivor/multi/report once, under a coarse lock.

    This must normally be run after all shards have finished.  It intentionally
    avoids per-shard concurrent overwrite of survivor/multi/report files.
    """
    FINALIZE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    try:
        fd = os.open(str(FINALIZE_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"pid={os.getpid()} created_at={runner.utc_now()}\n".encode("utf-8"))
    except FileExistsError:
        print(
            json.dumps(
                {
                    "event": "lr8d_multi6174_finalize_skipped_lock_exists",
                    "run_id": base.RUN_ID,
                    "lock_path": str(FINALIZE_LOCK_PATH),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 0

    try:
        base.write_survivors_and_report(all_symbols, timing)
        _write_progress(
            {
                "run_id": base.RUN_ID,
                "shard_index": int(args.shard_index),
                "shard_count": int(args.shard_count),
                "early_prune_enabled": bool(EARLY_PRUNE_ENABLED),
                "global_completed_period_rows": len(runner.completed_keys(runner.TOPN_PATH)),
                "global_pruned_ticker_rows": _line_count(PRUNED_PATH),
                "global_failure_rows": _line_count(FAILURES_PATH),
                "updated_at": runner.utc_now(),
                "status": "finalize_done",
            }
        )
        print(
            json.dumps(
                {
                    "event": "lr8d_multi6174_finalize_done",
                    "run_id": base.RUN_ID,
                    "output_dir": str(base.OUT_DIR),
                    "topn_rows": _line_count(runner.TOPN_PATH),
                    "pruned_rows": _line_count(PRUNED_PATH),
                    "failure_rows": _line_count(FAILURES_PATH),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    except Exception as exc:
        _append_failure(
            _failure_row(
                ticker="__REPORT__",
                split_label="report",
                stage="write_survivors_and_report",
                exc=exc,
                shard_count=args.shard_count,
                shard_index=args.shard_index,
            )
        )
        print(json.dumps({"event": "lr8d_multi6174_report_failed", "type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            FINALIZE_LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass


def main() -> int:
    args = _parse_args()
    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("--shard-index must satisfy 0 <= index < shard-count")

    _touch_outputs()
    all_symbols = base._read_ticker_file()
    symbols = all_symbols[args.shard_index :: args.shard_count]
    total_periods = len(all_symbols) * 4
    timing = _load_timing()

    if args.finalize_only:
        return _finalize_outputs(all_symbols, timing, args=args)

    print(
        json.dumps(
            {
                "event": "lr8d_multi6174_shard_start",
                "run_id": base.RUN_ID,
                "shard_index": args.shard_index,
                "shard_count": args.shard_count,
                "assigned_symbols": len(symbols),
                "all_symbols": len(all_symbols),
                "total_periods": total_periods,
                "output_dir": str(base.OUT_DIR),
                "early_prune_enabled": bool(EARLY_PRUNE_ENABLED),
                "finalize_on_shard_complete": bool(args.finalize_on_shard_complete or FINALIZE_ON_SHARD_COMPLETE),
                "early_prune_gate": {
                    "min_trades": int(runner.MIN_TRADES),
                    "min_member_score": float(runner.MIN_MEMBER_SCORE),
                    "min_expectancy_pct": float(EARLY_PRUNE_MIN_EXPECTANCY_PCT),
                    "max_drawdown_pct_gt": float(EARLY_PRUNE_DD_CUTOFF),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )

    if args.stop_after_step0:
        symbols = symbols[:1]

    processed = 0
    failed = 0
    pruned = 0
    started = time.time()
    for local_idx, ticker in enumerate(symbols, 1):
        ticker = str(ticker).upper().strip()
        if not ticker:
            continue
        if EARLY_PRUNE_ENABLED and ticker in _already_pruned_tickers():
            pruned += 1
            print(json.dumps({"event": "lr8d_multi6174_ticker_skip_already_pruned", "ticker": ticker, "run_id": base.RUN_ID}, ensure_ascii=False), flush=True)
            continue
        try:
            ctx = runner.prepare_ticker_context(ticker)
            splits = runner.build_splits(ctx.get("data_min"), ctx.get("data_max"))
            if not splits:
                raise RuntimeError("no usable 4fold splits from ticker context")
        except Exception as exc:
            failed += 1
            _append_failure(_failure_row(ticker=ticker, split_label="context", stage="prepare_ticker_context", exc=exc, shard_count=args.shard_count, shard_index=args.shard_index))
            print(json.dumps({"event": "lr8d_multi6174_context_failed", "ticker": ticker, "type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), flush=True)
            continue

        existing_rows = _load_existing_rows_for_ticker(ticker) if EARLY_PRUNE_ENABLED else {}
        ticker_pruned = False
        for split_idx, split in enumerate(splits, 1):
            key = f"{ticker}|{split['label']}"
            split_label = str(split.get("label") or "")
            try:
                done = runner.completed_keys(runner.TOPN_PATH)
                if key in done:
                    existing_row = existing_rows.get(split_label)
                    if EARLY_PRUNE_ENABLED and existing_row is not None and not _period_row_passes_gate(existing_row):
                        _append_pruned(_pruned_row(ticker=ticker, split_label=split_label, row=existing_row, reason="existing_period_failed_gate", shard_count=args.shard_count, shard_index=args.shard_index))
                        pruned += 1
                        ticker_pruned = True
                        print(json.dumps({"event": "lr8d_multi6174_ticker_pruned_existing_period", "ticker": ticker, "split": split_label, "run_id": base.RUN_ID}, ensure_ascii=False), flush=True)
                        break
                    continue
                seed = 20260630 + (args.shard_index + 1) * 1_000_000 + local_idx * 100 + split_idx
                print(
                    json.dumps(
                        {
                            "event": "lr8d_multi6174_period_start",
                            "run_id": base.RUN_ID,
                            "shard_index": args.shard_index,
                            "shard_count": args.shard_count,
                            "ticker": ticker,
                            "split": split["label"],
                            "completed_period_rows_before": len(done),
                            "total_periods": total_periods,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                row = runner.run_one_period(ticker, ctx, split, seed=seed)
                rulebook_rows = row.pop("_rulebooks", [])
                trade_rows = row.pop("_trades", [])
                runner.append_jsonl(runner.TOPN_PATH, row)
                for rr in rulebook_rows:
                    runner.append_jsonl(runner.RULEBOOKS_PATH, rr)
                for tr in trade_rows:
                    runner.append_jsonl(runner.TRADES_PATH, tr)
                processed += 1

                if EARLY_PRUNE_ENABLED and not _period_row_passes_gate(row):
                    _append_pruned(_pruned_row(ticker=ticker, split_label=split_label, row=row, reason="new_period_failed_gate", shard_count=args.shard_count, shard_index=args.shard_index))
                    pruned += 1
                    ticker_pruned = True
                    print(
                        json.dumps(
                            {
                                "event": "lr8d_multi6174_ticker_pruned_new_period",
                                "run_id": base.RUN_ID,
                                "ticker": ticker,
                                "split": split_label,
                                "processed_periods_before_prune": split_idx,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    break
            except Exception as exc:
                failed += 1
                _append_failure(_failure_row(ticker=ticker, split_label=str(split.get("label") or ""), stage="run_one_period", exc=exc, shard_count=args.shard_count, shard_index=args.shard_index))
                print(json.dumps({"event": "lr8d_multi6174_period_failed", "ticker": ticker, "split": split.get("label"), "type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), flush=True)
                ticker_pruned = bool(EARLY_PRUNE_ENABLED)
                if EARLY_PRUNE_ENABLED:
                    _append_pruned(_pruned_row(ticker=ticker, split_label=str(split.get("label") or ""), row=None, reason="period_exception_failed_gate", shard_count=args.shard_count, shard_index=args.shard_index))
                    pruned += 1
                    break
                continue

        if ticker_pruned:
            continue

        if local_idx % 5 == 0:
            _write_progress(
                {
                    "run_id": base.RUN_ID,
                    "shard_index": args.shard_index,
                    "shard_count": args.shard_count,
                    "early_prune_enabled": bool(EARLY_PRUNE_ENABLED),
                    "local_symbols_done_in_this_shard": local_idx,
                    "assigned_symbols": len(symbols),
                    "processed_periods_in_this_process": processed,
                    "pruned_tickers_in_this_process": pruned,
                    "failed_events_in_this_process": failed,
                    "global_completed_period_rows": len(runner.completed_keys(runner.TOPN_PATH)),
                    "global_pruned_ticker_rows": _line_count(PRUNED_PATH),
                    "global_failure_rows": _line_count(FAILURES_PATH),
                    "elapsed_seconds": round(time.time() - started, 3),
                    "updated_at": runner.utc_now(),
                }
            )

    should_finalize = bool(args.finalize_on_shard_complete or FINALIZE_ON_SHARD_COMPLETE)
    if should_finalize:
        finalize_rc = _finalize_outputs(all_symbols, timing, args=args)
        if finalize_rc != 0:
            failed += 1
    else:
        print(
            json.dumps(
                {
                    "event": "lr8d_multi6174_finalize_skipped",
                    "run_id": base.RUN_ID,
                    "reason": "finalize_only_required_after_all_shards",
                    "hint": "run this script once with --finalize-only after all shard processes finish",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    _write_progress(
        {
            "run_id": base.RUN_ID,
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "early_prune_enabled": bool(EARLY_PRUNE_ENABLED),
            "assigned_symbols": len(symbols),
            "processed_periods_in_this_process": processed,
            "pruned_tickers_in_this_process": pruned,
            "failed_events_in_this_process": failed,
            "global_completed_period_rows": len(runner.completed_keys(runner.TOPN_PATH)),
            "global_pruned_ticker_rows": _line_count(PRUNED_PATH),
            "global_failure_rows": _line_count(FAILURES_PATH),
            "elapsed_seconds": round(time.time() - started, 3),
            "updated_at": runner.utc_now(),
            "status": "shard_done" if should_finalize else "shard_done_no_finalize",
        }
    )
    print(
        json.dumps(
            {
                "event": "lr8d_multi6174_shard_done",
                "run_id": base.RUN_ID,
                "shard_index": args.shard_index,
                "processed_periods": processed,
                "pruned_tickers": pruned,
                "failed_events": failed,
                "global_completed_period_rows": len(runner.completed_keys(runner.TOPN_PATH)),
                "global_pruned_ticker_rows": _line_count(PRUNED_PATH),
                "output_dir": str(base.OUT_DIR),
                "finalized": should_finalize,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
