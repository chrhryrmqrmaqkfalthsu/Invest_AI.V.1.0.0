from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.pipeline.run_full_training_candidates as ftc  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_load_candidate_tickers_filters_cutoff_and_dedupes() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "candidates.json"
        payload = {
            "run_id": "source",
            "candidates": [
                {"ticker": "AAA", "stock_score": 61.0, "pass_count": 2},
                {"ticker": "BBB", "stock_score": 59.9, "pass_count": 1},
                {"ticker": "aaa", "stock_score": 99.0, "pass_count": 3},
                {"ticker": "CCC", "stock_score": 60.0, "pass_count": 1},
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        rows = ftc.load_candidate_tickers(path, cutoff=60.0)
        assert_true([r["ticker"] for r in rows] == ["AAA", "CCC"], "must filter cutoff and dedupe preserving order")
        assert_true(rows[0]["stock_score"] == 61.0, "score must be preserved")


def test_progress_save_load_and_resume_done_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = ftc.PIPELINE_ROOT
        ftc.PIPELINE_ROOT = Path(td)
        try:
            candidates = [
                {"ticker": "AAA", "stock_score": 61.0},
                {"ticker": "BBB", "stock_score": 62.0},
                {"ticker": "CCC", "stock_score": 63.0},
            ]
            progress = ftc.initialize_progress(candidates, "run1", source_path="source.json")
            ftc.set_ticker_status(progress, "AAA", "DONE", {"ticker": "AAA", "status": "DONE"})
            ftc.set_ticker_status(progress, "BBB", "ERROR", {"ticker": "BBB", "status": "ERROR"})
            ftc.save_progress("run1", progress)
            loaded = ftc.load_progress("run1")
            assert_true(loaded["counts"]["done"] == 1, "done count must persist")
            assert_true(loaded["counts"]["error"] == 1, "error count must persist")
            pending = ftc.select_pending_tickers(candidates, loaded, resume=True)
            assert_true(pending == ["BBB", "CCC"], "resume must skip DONE only and retry ERROR/PENDING")
            no_resume = ftc.select_pending_tickers(candidates, loaded, resume=False)
            assert_true(no_resume == ["AAA", "BBB", "CCC"], "no-resume must select all")
        finally:
            ftc.PIPELINE_ROOT = old_root


def test_summarize_batch_counts_scores_and_zero_qualified() -> None:
    progress = {
        "tickers": {
            "AAA": {
                "status": "DONE",
                "result": {
                    "ticker": "AAA",
                    "status": "DONE",
                    "member_count": 40,
                    "qualified_count": 10,
                    "member_score_min": 0.1,
                    "member_score_p50": 0.5,
                    "member_score_max": 0.9,
                    "elapsed_sec": 100.0,
                },
            },
            "BBB": {
                "status": "DONE",
                "result": {
                    "ticker": "BBB",
                    "status": "DONE",
                    "member_count": 40,
                    "qualified_count": 0,
                    "member_score_min": 0.0,
                    "member_score_p50": 0.2,
                    "member_score_max": 0.4,
                    "elapsed_sec": 200.0,
                },
            },
            "CCC": {
                "status": "ERROR",
                "result": {
                    "ticker": "CCC",
                    "status": "ERROR",
                    "error": {"type": "RuntimeError", "message": "boom"},
                },
            },
        }
    }
    summary = ftc.summarize_batch(progress)
    assert_true(summary["total"] == 3, "total must match")
    assert_true(summary["done_count"] == 2, "done count must match")
    assert_true(summary["error_count"] == 1, "error count must match")
    assert_true(summary["qualified_zero_tickers"] == ["BBB"], "zero qualified ticker must be listed")
    qdist = summary["qualified_count_distribution"]
    assert_true(qdist["min"] == 0.0 and qdist["max"] == 10.0, "qualified distribution must match")
    mdist = summary["member_score_distribution"]
    assert_true(mdist["count"] == 6, "member score summary endpoints must aggregate")


def test_process_candidate_ticker_uses_mock_and_saves() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = ftc.PIPELINE_ROOT
        ftc.PIPELINE_ROOT = Path(td)
        try:
            calls = {"train": 0, "save": 0}

            def fake_train(ticker, run_id=None, ga_config=None):
                calls["train"] += 1
                assert_true(ticker == "AAA", "ticker must be passed")
                assert_true(run_id == "run1", "run_id must be passed")
                assert_true(ga_config is not None, "ga_config must be passed")
                return {
                    "ticker": ticker,
                    "run_id": run_id,
                    "member_count": 2,
                    "qualified_count": 1,
                    "member_score_distribution": {"min": 0.1, "median": 0.5, "max": 0.9, "qualified_min": 0.9, "qualified_median": 0.9, "qualified_max": 0.9},
                    "elapsed_sec": 1.2,
                }

            def fake_save(result, output_dir):
                calls["save"] += 1
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                fp = Path(output_dir) / "full_training.json"
                mp = Path(output_dir) / "members.jsonl"
                fp.write_text(json.dumps(result), encoding="utf-8")
                mp.write_text("{}\n", encoding="utf-8")
                return {"full_training": str(fp), "members": str(mp)}

            result = ftc.process_candidate_ticker("AAA", "run1", ga_mode="smoke", full_training_fn=fake_train, save_artifacts_fn=fake_save)
            assert_true(result["status"] == "DONE", "mock process must finish")
            assert_true(result["member_count"] == 2, "member count must summarize")
            assert_true(result["qualified_count"] == 1, "qualified count must summarize")
            assert_true(Path(result["outputs"]["full_training"]).exists(), "full_training output must exist")
            assert_true(calls == {"train": 1, "save": 1}, "mock functions must be called once")
        finally:
            ftc.PIPELINE_ROOT = old_root


def run_all() -> None:
    tests = [
        test_load_candidate_tickers_filters_cutoff_and_dedupes,
        test_progress_save_load_and_resume_done_only,
        test_summarize_batch_counts_scores_and_zero_qualified,
        test_process_candidate_ticker_uses_mock_and_saves,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL FULL TRAINING CANDIDATES TESTS PASSED")


if __name__ == "__main__":
    run_all()
