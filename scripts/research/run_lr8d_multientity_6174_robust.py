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

주의:
- 이 파일은 research artifact만 생성한다.
- live parameters 또는 data/symbols/parameters.json을 수정하지 않는다.
- live trading runner를 시작/중지하지 않는다.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research import run_lr8d_multientity_6174 as base

runner = base.runner
FAILURES_PATH = base.OUT_DIR / f"{base.RUN_PREFIX}_failures.jsonl"
PROGRESS_PATH = base.OUT_DIR / f"{base.RUN_PREFIX}_progress.json"


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


def _write_progress(payload: dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": "LR8D 6174 다중개체 robust 백그라운드 실행의 최신 진행 상태입니다.",
        **payload,
    }
    PROGRESS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _touch_outputs() -> None:
    base.OUT_DIR.mkdir(parents=True, exist_ok=True)
    base.write_readme()
    for path in [runner.TOPN_PATH, runner.RULEBOOKS_PATH, runner.TRADES_PATH, runner.SURVIVORS_PATH, FAILURES_PATH]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)


def _append_failure(row: dict[str, Any]) -> None:
    runner.append_jsonl(FAILURES_PATH, row)


def main() -> int:
    args = runner.parse_args()
    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("--shard-index must satisfy 0 <= index < shard-count")

    _touch_outputs()
    all_symbols = base._read_ticker_file()
    symbols = all_symbols[args.shard_index :: args.shard_count]
    total_periods = len(all_symbols) * 4
    timing = None
    if runner.TIMING_PATH.exists() and runner.TIMING_PATH.read_text(encoding="utf-8").strip():
        try:
            timing = json.loads(runner.TIMING_PATH.read_text(encoding="utf-8"))
        except Exception:
            timing = None

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
    started = time.time()
    for local_idx, ticker in enumerate(symbols, 1):
        ticker = str(ticker).upper().strip()
        if not ticker:
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

        for split_idx, split in enumerate(splits, 1):
            key = f"{ticker}|{split['label']}"
            try:
                done = runner.completed_keys(runner.TOPN_PATH)
                if key in done:
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
            except Exception as exc:
                failed += 1
                _append_failure(_failure_row(ticker=ticker, split_label=str(split.get("label") or ""), stage="run_one_period", exc=exc, shard_count=args.shard_count, shard_index=args.shard_index))
                print(json.dumps({"event": "lr8d_multi6174_period_failed", "ticker": ticker, "split": split.get("label"), "type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), flush=True)
                continue

        if local_idx % 5 == 0:
            _write_progress(
                {
                    "run_id": base.RUN_ID,
                    "shard_index": args.shard_index,
                    "shard_count": args.shard_count,
                    "local_symbols_done_in_this_shard": local_idx,
                    "assigned_symbols": len(symbols),
                    "processed_periods_in_this_process": processed,
                    "failed_events_in_this_process": failed,
                    "global_completed_period_rows": len(runner.completed_keys(runner.TOPN_PATH)),
                    "global_failure_rows": sum(1 for line in FAILURES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()) if FAILURES_PATH.exists() else 0,
                    "elapsed_seconds": round(time.time() - started, 3),
                    "updated_at": runner.utc_now(),
                }
            )

    try:
        base.write_survivors_and_report(all_symbols, timing)
    except Exception as exc:
        failed += 1
        _append_failure(_failure_row(ticker="__REPORT__", split_label="report", stage="write_survivors_and_report", exc=exc, shard_count=args.shard_count, shard_index=args.shard_index))
        print(json.dumps({"event": "lr8d_multi6174_report_failed", "type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), flush=True)

    _write_progress(
        {
            "run_id": base.RUN_ID,
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
            "assigned_symbols": len(symbols),
            "processed_periods_in_this_process": processed,
            "failed_events_in_this_process": failed,
            "global_completed_period_rows": len(runner.completed_keys(runner.TOPN_PATH)),
            "global_failure_rows": sum(1 for line in FAILURES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()) if FAILURES_PATH.exists() else 0,
            "elapsed_seconds": round(time.time() - started, 3),
            "updated_at": runner.utc_now(),
            "status": "shard_done",
        }
    )
    print(
        json.dumps(
            {
                "event": "lr8d_multi6174_shard_done",
                "run_id": base.RUN_ID,
                "shard_index": args.shard_index,
                "processed_periods": processed,
                "failed_events": failed,
                "global_completed_period_rows": len(runner.completed_keys(runner.TOPN_PATH)),
                "output_dir": str(base.OUT_DIR),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
