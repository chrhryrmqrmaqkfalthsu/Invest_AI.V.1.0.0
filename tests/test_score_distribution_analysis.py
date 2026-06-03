from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.analyze_score_distribution import (  # noqa: E402
    analyze_rows,
    dedupe_latest,
    distribution,
    normalize_rows,
    percentile,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_percentile_and_distribution() -> None:
    values = [0.0, 10.0, 30.0]
    assert_true(percentile(values, 0.0) == 0.0, "min percentile must be 0")
    assert_true(percentile(values, 0.5) == 10.0, "median must be 10")
    assert_true(percentile(values, 1.0) == 30.0, "max percentile must be 30")
    d = distribution(values)
    assert_true(d["count"] == 3, "distribution count must be 3")
    assert_true(d["avg"] == 40.0 / 3.0, "distribution avg must match")


def test_normalize_and_dedupe_latest() -> None:
    batches = [
        {"_run_id": "r1", "results": [{"ticker": "MSFT", "final_status": "ROLLING_DONE", "stock_score": 10.0}]},
        {"_run_id": "r2", "results": [{"ticker": "MSFT", "final_status": "ROLLING_DONE", "stock_score": 20.0}]},
    ]
    rows = normalize_rows(batches)
    assert_true(len(rows) == 2, "normalize must preserve both rows before dedupe")
    latest = dedupe_latest(rows)
    assert_true(len(latest) == 1, "dedupe must keep one row per ticker")
    assert_true(latest[0]["stock_score_float"] == 20.0, "dedupe must keep later run row")


def test_cutoff_and_group_analysis() -> None:
    rows = [
        {"ticker": "MSFT", "final_status": "ROLLING_DONE", "stock_score_float": 70.0, "pass_count_int": 2, "sector": "Technology", "cap_bucket": "Mega"},
        {"ticker": "GE", "final_status": "ROLLING_DONE", "stock_score_float": 0.0, "pass_count_int": 0, "excluded": True, "sector": "Industrials", "cap_bucket": "Large"},
        {"ticker": "BIS", "final_status": "SCREENED_OUT", "screening_reason_code": "ADV_BELOW_MIN", "sector": "ETF/Edge", "cap_bucket": "Low-liquidity edge"},
    ]
    a = analyze_rows(rows)
    assert_true(a["total"] == 3, "total must be 3")
    assert_true(a["screened_out_count"] == 1, "screened out count must be 1")
    assert_true(a["rolling_done_count"] == 2, "rolling done count must be 2")
    assert_true(a["zero_score_count"] == 1, "zero score count must be 1")
    scenarios = {x["scenario"]: x for x in a["cutoff_scenarios"]}
    assert_true(scenarios["pass_count >= 2"]["tickers"] == ["MSFT"], "pass_count>=2 must select MSFT")
    assert_true(scenarios["stock_score > 0"]["tickers"] == ["MSFT"], "score>0 must select MSFT")


def run_all() -> None:
    tests = [
        test_percentile_and_distribution,
        test_normalize_and_dedupe_latest,
        test_cutoff_and_group_analysis,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL SCORE DISTRIBUTION ANALYSIS TESTS PASSED")


if __name__ == "__main__":
    run_all()
