from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.metadata import (  # noqa: E402
    build_metadata,
    canonical_rulebook_dict,
    compute_member_hash,
    compute_rulebook_hash,
    ga_config_to_dict,
)


@dataclass
class DummyRulebook:
    threshold: float
    lookback: int
    direction: str
    fitness: float = 0.0
    win_rate: float = 0.0
    generated_at: str = ""


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_same_params_different_performance_same_hash() -> None:
    rb_a = {
        "threshold": 1.25,
        "lookback": 20,
        "direction": "long",
        "fitness": 10.0,
        "win_rate": 55.0,
        "avg_return_pct": 1.2,
        "expectancy_pct": 0.4,
        "max_drawdown_pct": -8.0,
        "trade_count": 31,
        "generated_at": "2026-01-01T00:00:00Z",
    }
    rb_b = {
        "threshold": 1.25,
        "lookback": 20,
        "direction": "long",
        "fitness": 99.0,
        "win_rate": 70.0,
        "avg_return_pct": 3.5,
        "expectancy_pct": 2.1,
        "max_drawdown_pct": -3.0,
        "trade_count": 88,
        "generated_at": "2026-06-01T00:00:00Z",
    }

    hash_a = compute_rulebook_hash(rb_a)
    hash_b = compute_rulebook_hash(rb_b)
    assert_true(hash_a == hash_b, "same strategy params must produce same rulebook_hash")

    canonical = canonical_rulebook_dict(rb_a)
    for excluded in ("fitness", "win_rate", "avg_return_pct", "expectancy_pct", "max_drawdown_pct", "trade_count", "generated_at"):
        assert_true(excluded not in canonical, f"{excluded} must be excluded from canonical hash input")


def test_param_change_changes_hash() -> None:
    rb_a = {"threshold": 1.25, "lookback": 20, "direction": "long", "fitness": 10.0}
    rb_b = {"threshold": 1.30, "lookback": 20, "direction": "long", "fitness": 10.0}

    assert_true(
        compute_rulebook_hash(rb_a) != compute_rulebook_hash(rb_b),
        "changing a strategy parameter must change rulebook_hash",
    )


def test_build_metadata_required_keys() -> None:
    rb = DummyRulebook(
        threshold=1.25,
        lookback=20,
        direction="long",
        fitness=123.0,
        win_rate=65.0,
        generated_at="2026-01-01T00:00:00Z",
    )

    meta = build_metadata(
        source="learn_full",
        ticker="NVDA",
        fitness_mode="swing",
        data_start="2020-01-01",
        data_end="2025-12-31",
        train_period=["2020-01-01", "2025-12-31"],
        test_period=[],
        oos_periods=[],
        ga={"population": 40, "generations": 50, "seed": 42},
        rulebook=rb,
        validation={"validated": True, "method": "rolling_true_wf"},
    )

    required = {
        "run_id",
        "created_at",
        "source",
        "ticker",
        "fitness_mode",
        "data_start",
        "data_end",
        "train_period",
        "test_period",
        "oos_periods",
        "ga",
        "rulebook_hash",
        "member_hash",
        "validation",
        "feature_lag",
    }
    assert_true(required.issubset(meta.keys()), "metadata must contain all required standard keys")
    assert_true(bool(meta["run_id"]), "run_id must be populated")
    assert_true(meta["created_at"].endswith("Z"), "created_at must be UTC ISO-8601 string ending with Z")
    assert_true(meta["source"] == "learn_full", "source must be preserved")
    assert_true(meta["ticker"] == "NVDA", "ticker must be preserved")
    assert_true(meta["ga"]["population"] == 40, "ga.population must be preserved")
    assert_true(meta["feature_lag"] == {"ticker_sentiment_days": 1, "market_events_days": 1}, "default feature lag must be 1 day")
    assert_true(re.fullmatch(r"[0-9a-f]{64}", meta["rulebook_hash"]), "rulebook_hash must be sha256 hex")
    assert_true(meta["rulebook_hash"] == meta["member_hash"], "member_hash should reuse rulebook_hash in phase 1")


def test_none_and_empty_inputs_are_safe() -> None:
    assert_true(canonical_rulebook_dict(None) == {}, "None canonical rulebook must be empty dict")
    assert_true(re.fullmatch(r"[0-9a-f]{64}", compute_rulebook_hash(None)) is not None, "None hash must still be sha256 hex")
    assert_true(compute_member_hash(None) == compute_rulebook_hash(None), "None member hash must be safe")
    assert_true(ga_config_to_dict(None, None) == {}, "None GA config/result must return empty dict")

    meta = build_metadata()
    assert_true(meta["source"] == "", "missing source must default to empty string")
    assert_true(meta["ticker"] == "", "missing ticker must default to empty string")
    assert_true(meta["ga"] == {}, "missing ga must default to empty dict")
    assert_true(meta["feature_lag"] == {"ticker_sentiment_days": 1, "market_events_days": 1}, "missing feature_lag must use default")


def run_all() -> None:
    tests = [
        test_same_params_different_performance_same_hash,
        test_param_change_changes_hash,
        test_build_metadata_required_keys,
        test_none_and_empty_inputs_are_safe,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL METADATA TESTS PASSED")


if __name__ == "__main__":
    run_all()
