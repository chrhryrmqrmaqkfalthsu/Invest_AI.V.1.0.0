from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from engine.portfolio.daily_signal_replay import (
    ENTRY_DIFF_ABS_TOL,
    ENTRY_DIFF_PCT_TOL,
    _entry_match_ok,
    build_rulebook_map,
    dry_run_plan,
    entry_diff_stats,
    proxy_disagreement_rate,
)


def test_build_rulebook_map_requires_stage2_artifact_no_fallback(tmp_path: Path):
    p = tmp_path / "topn_rulebooks.jsonl"
    rulebook = {
        "ticker": "AAA",
        "asset_type": "us_stock",
        "direction": "long",
        "signal_threshold": 1.0,
    }
    p.write_text(
        json.dumps({"member_hash": "m1", "rulebook_hash": "r1", "rulebook": rulebook}) + "\n",
        encoding="utf-8",
    )
    rb_map = build_rulebook_map(p)
    assert ("m1", "r1") in rb_map
    assert rb_map[("m1", "r1")].ticker == "AAA"
    assert build_rulebook_map(tmp_path / "missing.jsonl") == {}


def test_entry_match_tolerance_logic():
    assert _entry_match_ok({"entry_strength_diff_abs": ENTRY_DIFF_ABS_TOL / 2, "entry_strength_diff_pct": 10.0})
    assert _entry_match_ok({"entry_strength_diff_abs": 1.0, "entry_strength_diff_pct": ENTRY_DIFF_PCT_TOL / 2})
    assert not _entry_match_ok({"entry_strength_diff_abs": 1.0, "entry_strength_diff_pct": 1.0})


def test_entry_diff_stats_reports_failed_samples():
    rows = [
        {"entry_strength_diff_abs": 0.0, "entry_strength_diff_pct": 0.0, "lot_id": "ok"},
        {"entry_strength_diff_abs": 0.5, "entry_strength_diff_pct": 0.5, "lot_id": "bad"},
    ]
    stats = entry_diff_stats(rows)
    assert stats["pass_count"] == 1
    assert stats["fail_count"] == 1
    assert stats["failed_samples"][0]["lot_id"] == "bad"


def test_proxy_disagreement_rate():
    rows = [
        {"strength_decay_pct": 20.0, "price_path_proxy_baseline": 1.0},
        {"strength_decay_pct": 5.0, "price_path_proxy_baseline": 5.0},
        {"strength_decay_pct": 20.0, "price_path_proxy_baseline": 5.0},
    ]
    out = proxy_disagreement_rate(rows, decay_threshold_pct=15.0, proxy_threshold_pct=3.0)
    assert out["count"] == 3
    assert out["disagreement_count"] == 2


def test_dry_run_plan_detects_missing_rulebook(tmp_path: Path):
    trades = tmp_path / "trades.jsonl"
    rulebooks = tmp_path / "topn_rulebooks.jsonl"
    ohlcv = tmp_path / "ohlcv"
    ohlcv.mkdir()
    trades.write_text(
        json.dumps({
            "ticker": "AAA",
            "member_hash": "m1",
            "rulebook_hash": "missing",
            "entry_signal_date": "2024-01-02",
            "entry_fill_date": "2024-01-03",
            "exit_date": "2024-01-10",
            "entry_signal_score": 2.0,
            "entry_signal_threshold": 1.0,
        }) + "\n",
        encoding="utf-8",
    )
    rulebooks.write_text("", encoding="utf-8")
    summary = dry_run_plan(trades_jsonl=trades, rulebooks_jsonl=rulebooks, ohlcv_cache=ohlcv, limit=10)
    assert summary["lots_sampled"] == 1
    assert summary["missing_rulebook_count"] == 1
    assert summary["fallback_used"] is False
    assert "forbidden" in summary["fallback_policy"]
