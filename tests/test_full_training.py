from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine.pipeline.orchestrator as orchestrator  # noqa: E402
from engine.pipeline.full_training import (  # noqa: E402
    FULL_TRAINING_STOCK_SCORE_CUTOFF,
    build_member_payload,
    full_training_gate_from_rolling,
    member_score_distribution,
)
from engine.pipeline.orchestrator import process_ticker  # noqa: E402
from engine.pipeline.scoring import score_full_training_members  # noqa: E402
from engine.strategies.rulebook import default_rulebook  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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


def fake_rolling(score: float, excluded: bool = False):
    def _fn(ticker, context=None):
        return {
            "ticker": ticker,
            "stock_score": {
                "stock_score": score,
                "consistency_score": 40.0,
                "quality_score": max(0.0, score - 40.0),
                "liquidity_weight": 1.0,
                "excluded": excluded,
                "exclude_reason": "NO_OOS_PASS" if excluded else "",
                "raw_metrics": {"pass_count": 2 if score >= 60 else 1},
            },
        }
    return _fn


def fake_full_training(ticker, context=None, run_id=None):
    return {
        "ticker": ticker,
        "stage": "full_training",
        "run_id": run_id,
        "train_period": ["2020-01-01", "2025-12-31"],
        "ga": {"generations_run": 1, "population_size": 1},
        "member_count": 1,
        "qualified_count": 1,
        "member_score_distribution": {"count": 1, "min": 1.0, "median": 1.0, "max": 1.0, "qualified_count": 1},
        "top_members": [{"rank": 1, "qualified": True, "member_score": 1.0, "trade_count": 12, "expectancy_pct": 1.2, "profit_factor": 1.5}],
        "members": [
            {
                "rank": 1,
                "member_hash": "abc",
                "qualified": True,
                "member_score": 1.0,
                "trade_count": 12,
                "expectancy_pct": 1.2,
                "profit_factor": 1.5,
            }
        ],
    }


def test_build_member_payload_uses_backtest_metrics_and_hash() -> None:
    rb = default_rulebook("TEST", asset_type="us_stock", direction="long")
    rb.signal_threshold = 2.25
    rb.fitness = 999.0
    result = SimpleNamespace(
        trade_count=12,
        win_rate=58.3,
        expectancy_pct=1.7,
        avg_return_pct=1.6,
        profit_factor=1.8,
        max_drawdown_pct=-9.5,
        sharpe_like=1.2,
        win_count=7,
        loss_count=5,
    )
    payload = build_member_payload(rb, result, rank=3)
    assert_true(payload["rank"] == 3, "rank must be preserved")
    assert_true(payload["fitness"] == 999.0, "fitness must be preserved separately")
    assert_true(payload["trade_count"] == 12, "trade_count must come from backtest result")
    assert_true(payload["expectancy_pct"] == 1.7, "expectancy must come from backtest result")
    assert_true(payload["profit_factor"] == 1.8, "profit_factor must come from backtest result")
    assert_true(isinstance(payload["member_hash"], str) and len(payload["member_hash"]) == 64, "member_hash must be sha256 hex")
    assert_true("rulebook" in payload and payload["rulebook"]["signal_threshold"] == 2.25, "rulebook dict must be included")


def test_full_training_gate_cutoff_and_excluded() -> None:
    below = full_training_gate_from_rolling({"stock_score": {"stock_score": 59.999, "excluded": False}})
    at_cutoff = full_training_gate_from_rolling({"stock_score": {"stock_score": FULL_TRAINING_STOCK_SCORE_CUTOFF, "excluded": False}})
    excluded = full_training_gate_from_rolling({"stock_score": {"stock_score": 100.0, "excluded": True, "exclude_reason": "NO_OOS_PASS"}})
    assert_true(below["should_run"] is False and below["reason_code"] == "BELOW_CUTOFF", "score below 60 must skip")
    assert_true(at_cutoff["should_run"] is True, "score 60 must run")
    assert_true(excluded["should_run"] is False and excluded["rolling_excluded"] is True, "excluded rolling result must skip")


def test_score_full_training_members_accepts_full_training_payload_shape() -> None:
    rb1 = default_rulebook("TEST", asset_type="us_stock", direction="long")
    rb2 = default_rulebook("TEST", asset_type="us_stock", direction="long")
    rb2.signal_threshold = 3.0
    p1 = build_member_payload(
        rb1,
        SimpleNamespace(trade_count=11, win_rate=60.0, expectancy_pct=1.0, avg_return_pct=1.0, profit_factor=1.3, max_drawdown_pct=-5.0),
        1,
    )
    p2 = build_member_payload(
        rb2,
        SimpleNamespace(trade_count=5, win_rate=40.0, expectancy_pct=-0.5, avg_return_pct=-0.5, profit_factor=0.8, max_drawdown_pct=-20.0),
        2,
    )
    scored = score_full_training_members([p1, p2])
    assert_true(len(scored) == 2, "both members must be preserved")
    assert_true(scored[0]["qualified"] is True, "first member must qualify")
    assert_true(scored[1]["qualified"] is False, "second member must be preserved as unqualified")
    assert_true(0.0 <= scored[0]["member_score"] <= 1.0, "member_score must be normalized")
    dist = member_score_distribution(scored)
    assert_true(dist["count"] == 2 and dist["qualified_count"] == 1, "distribution must count qualified members")


def test_orchestrator_skips_full_training_below_cutoff() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = orchestrator.PIPELINE_ROOT
        orchestrator.PIPELINE_ROOT = Path(td)
        calls = {"full_training": 0}
        try:
            def ft(ticker, context=None, run_id=None):
                calls["full_training"] += 1
                return fake_full_training(ticker, context, run_id)

            result = process_ticker(
                "AAA",
                "run_below",
                run_full_training=True,
                screening_fn=fake_screening,
                rolling_fn=fake_rolling(59.0),
                full_training_fn=ft,
            )
            assert_true(result["final_status"] == "ROLLING_DONE", "below cutoff must stop at rolling")
            assert_true(result["full_training"]["executed"] is False, "full training must be skipped")
            assert_true(result["full_training"]["reason_code"] == "BELOW_CUTOFF", "skip reason must be BELOW_CUTOFF")
            assert_true(calls["full_training"] == 0, "full training function must not be called")
        finally:
            orchestrator.PIPELINE_ROOT = old_root


def test_orchestrator_runs_full_training_at_cutoff() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = orchestrator.PIPELINE_ROOT
        orchestrator.PIPELINE_ROOT = Path(td)
        calls = {"full_training": 0}
        try:
            def ft(ticker, context=None, run_id=None):
                calls["full_training"] += 1
                assert_true(context == {"marker": "CTX"}, "screening context must be reused")
                return fake_full_training(ticker, context, run_id)

            result = process_ticker(
                "AAA",
                "run_pass",
                run_full_training=True,
                screening_fn=fake_screening,
                rolling_fn=fake_rolling(60.0),
                full_training_fn=ft,
            )
            assert_true(result["final_status"] == "FULL_TRAINING_DONE", "score 60 must run full training")
            assert_true(result["final_stage"] == "full_training", "final stage must be full_training")
            assert_true(result["full_training"]["qualified_count"] == 1, "qualified count must be summarized")
            assert_true(calls["full_training"] == 1, "full training function must be called once")
            assert_true((Path(td) / "run_pass" / "AAA" / "members.jsonl").exists(), "members.jsonl must be saved")
            assert_true((Path(td) / "run_pass" / "AAA" / "full_training.json").exists(), "full_training.json must be saved")
        finally:
            orchestrator.PIPELINE_ROOT = old_root


def test_orchestrator_skips_full_training_when_rolling_excluded() -> None:
    with tempfile.TemporaryDirectory() as td:
        old_root = orchestrator.PIPELINE_ROOT
        orchestrator.PIPELINE_ROOT = Path(td)
        calls = {"full_training": 0}
        try:
            def ft(ticker, context=None, run_id=None):
                calls["full_training"] += 1
                return fake_full_training(ticker, context, run_id)

            result = process_ticker(
                "AAA",
                "run_excluded",
                run_full_training=True,
                screening_fn=fake_screening,
                rolling_fn=fake_rolling(100.0, excluded=True),
                full_training_fn=ft,
            )
            assert_true(result["final_status"] == "ROLLING_DONE", "excluded rolling must stop at rolling")
            assert_true(result["full_training"]["executed"] is False, "excluded rolling must skip full training")
            assert_true(result["full_training"]["gate"]["rolling_excluded"] is True, "gate must record excluded")
            assert_true(calls["full_training"] == 0, "full training must not run for excluded rolling")
        finally:
            orchestrator.PIPELINE_ROOT = old_root


def run_all() -> None:
    tests = [
        test_build_member_payload_uses_backtest_metrics_and_hash,
        test_full_training_gate_cutoff_and_excluded,
        test_score_full_training_members_accepts_full_training_payload_shape,
        test_orchestrator_skips_full_training_below_cutoff,
        test_orchestrator_runs_full_training_at_cutoff,
        test_orchestrator_skips_full_training_when_rolling_excluded,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL FULL TRAINING TESTS PASSED")


if __name__ == "__main__":
    run_all()
