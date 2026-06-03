from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine.pipeline.batch as batch  # noqa: E402
import engine.pipeline.orchestrator as orchestrator  # noqa: E402
from engine.pipeline.orchestrator import process_ticker  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_progress_atomic_write_and_load() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = batch.PIPELINE_ROOT
        batch.PIPELINE_ROOT = Path(td)
        try:
            progress = batch.initialize_progress(["AAA", "BBB"], "run1")
            batch.save_progress("run1", progress)
            path = batch.progress_path("run1")
            assert_true(path.exists(), "progress file must exist")
            assert_true(not path.with_name(path.name + ".tmp").exists(), "tmp file must be renamed away")
            loaded = batch.load_progress("run1")
            assert_true(loaded["run_id"] == "run1", "loaded run_id must match")
            assert_true(loaded["counts"]["total"] == 2, "counts.total must be 2")
        finally:
            batch.PIPELINE_ROOT = old_root


def test_resume_selects_only_non_terminal_tickers() -> None:
    progress = batch.initialize_progress(["AAA", "BBB", "CCC", "DDD"], "run2")
    batch.set_ticker_status(progress, "AAA", "ROLLING_DONE")
    batch.set_ticker_status(progress, "BBB", "SCREENED_OUT")
    batch.set_ticker_status(progress, "CCC", "ERROR")
    batch.set_ticker_status(progress, "DDD", "RUNNING")
    pending = batch.select_pending_tickers(["AAA", "BBB", "CCC", "DDD"], progress, resume=True)
    assert_true(pending == ["DDD"], "resume must skip terminal statuses only")
    all_pending = batch.select_pending_tickers(["AAA", "BBB"], progress, resume=False)
    assert_true(all_pending == ["AAA", "BBB"], "no-resume must run all requested tickers")


def test_summary_reason_counts_and_stock_score_distribution() -> None:
    results = [
        {
            "ticker": "AAA",
            "final_status": "ROLLING_DONE",
            "screening": {"reason_code": ""},
            "rolling": {"stock_score": 10.0, "excluded": False},
        },
        {
            "ticker": "BBB",
            "final_status": "ROLLING_DONE",
            "screening": {"reason_code": ""},
            "rolling": {"stock_score": 30.0, "excluded": False},
        },
        {
            "ticker": "CCC",
            "final_status": "SCREENED_OUT",
            "screening": {"reason_code": "ADV_BELOW_MIN"},
            "rolling": {},
        },
        {
            "ticker": "DDD",
            "final_status": "ERROR",
            "screening": {"reason_code": "ERROR"},
            "rolling": {},
        },
        {
            "ticker": "EEE",
            "final_status": "ROLLING_DONE",
            "screening": {"reason_code": ""},
            "rolling": {"stock_score": 0.0, "excluded": True},
        },
    ]
    summary = batch.summarize_results(results)
    assert_true(summary["rolling_done_count"] == 3, "rolling_done_count must be 3")
    assert_true(summary["screened_out_count"] == 1, "screened_out_count must be 1")
    assert_true(summary["error_count"] == 1, "error_count must be 1")
    assert_true(summary["screening_reason_counts"]["ADV_BELOW_MIN"] == 1, "ADV reason must count")
    dist = summary["stock_score_distribution"]
    assert_true(dist["count"] == 3, "three rolling scores must be included")
    assert_true(dist["min"] == 0.0, "min score must be 0")
    assert_true(dist["p50"] == 10.0, "median of [0,10,30] must be 10")
    assert_true(dist["max"] == 30.0, "max score must be 30")
    assert_true(dist["zero_score_count"] == 1, "zero score count must be 1")
    assert_true(dist["excluded_count"] == 1, "excluded count must be 1")


def test_process_ticker_screened_out_skips_rolling() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = orchestrator.PIPELINE_ROOT
        orchestrator.PIPELINE_ROOT = Path(td)
        try:
            calls = {"rolling": 0}

            def fake_screening(ticker, include_context=False):
                return {
                    "ticker": ticker,
                    "stage": "screening",
                    "passed": False,
                    "status": "FAIL",
                    "reason_code": "ADV_BELOW_MIN",
                    "adv_usd_252d": 1.0,
                    "liquidity_weight": 0.0,
                    "data": {"data_start": "2020-01-01", "data_end": "2026-01-01", "rows": 1000, "split_count": 3},
                    "sentiment": {"sentiment_days": 0, "has_sentiment": False},
                    "viability": {"executed": False},
                    "_context": {"should_not_be_used": True} if include_context else None,
                }

            def fake_rolling(ticker, context=None):
                calls["rolling"] += 1
                raise AssertionError("rolling must not be called for screened-out ticker")

            result = process_ticker(
                "AAA",
                "run3",
                screening_fn=fake_screening,
                rolling_fn=fake_rolling,
            )
            assert_true(result["final_status"] == "SCREENED_OUT", "screened out ticker must end as SCREENED_OUT")
            assert_true(result["reason_code"] == "ADV_BELOW_MIN", "reason must propagate")
            assert_true(calls["rolling"] == 0, "rolling must be skipped")
            assert_true((Path(td) / "run3" / "AAA" / "screening.json").exists(), "screening output must be saved")
            assert_true((Path(td) / "run3" / "AAA" / "final.json").exists(), "final output must be saved")
        finally:
            orchestrator.PIPELINE_ROOT = old_root


def test_process_ticker_passed_calls_rolling_with_context() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = orchestrator.PIPELINE_ROOT
        orchestrator.PIPELINE_ROOT = Path(td)
        try:
            seen_context = {"value": False}

            def fake_screening(ticker, include_context=False):
                return {
                    "ticker": ticker,
                    "stage": "screening",
                    "passed": True,
                    "status": "PASS",
                    "reason_code": "",
                    "adv_usd_252d": 100_000_000.0,
                    "liquidity_weight": 1.0,
                    "data": {"data_start": "2020-01-01", "data_end": "2026-01-01", "rows": 1000, "split_count": 3},
                    "sentiment": {"sentiment_days": 1, "has_sentiment": True},
                    "viability": {"executed": True, "trade_count": 5},
                    "_context": {"marker": "CTX"} if include_context else None,
                }

            def fake_rolling(ticker, context=None):
                seen_context["value"] = context == {"marker": "CTX"}
                return {
                    "ticker": ticker,
                    "stock_score": {
                        "stock_score": 42.0,
                        "consistency_score": 40.0,
                        "quality_score": 2.0,
                        "liquidity_weight": 1.0,
                        "excluded": False,
                        "exclude_reason": "",
                        "raw_metrics": {"pass_count": 2},
                    },
                }

            result = process_ticker(
                "AAA",
                "run4",
                screening_fn=fake_screening,
                rolling_fn=fake_rolling,
            )
            assert_true(result["final_status"] == "ROLLING_DONE", "passed ticker must run rolling")
            assert_true(seen_context["value"] is True, "rolling must receive screening context")
            assert_true(result["rolling"]["stock_score"] == 42.0, "stock score must be summarized")
            assert_true((Path(td) / "run4" / "AAA" / "rolling_validation.json").exists(), "rolling output must be saved")
        finally:
            orchestrator.PIPELINE_ROOT = old_root


def run_all() -> None:
    tests = [
        test_progress_atomic_write_and_load,
        test_resume_selects_only_non_terminal_tickers,
        test_summary_reason_counts_and_stock_score_distribution,
        test_process_ticker_screened_out_skips_rolling,
        test_process_ticker_passed_calls_rolling_with_context,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL BATCH TESTS PASSED")


if __name__ == "__main__":
    run_all()
