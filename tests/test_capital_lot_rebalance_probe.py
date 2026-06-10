from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from engine.portfolio.capital_lot_rebalance_probe import (
    LotProbeConfig,
    dry_run_plan,
    load_daily_signal_lookup,
    load_selected_rulebook_set,
    load_stage2_trade_candidates,
    signal_to_entry_weight,
    simulate_lot_rebalance,
)
from engine.portfolio.daily_signal_replay import assign_canonical_lot_ids


def _trade(ticker: str, idx: int, score: float, threshold: float = 1.0, exit_reason: str = "trailing") -> dict:
    row = {
        "ticker": ticker,
        "member_hash": f"member{idx:02d}",
        "rulebook_hash": f"rule{idx:02d}",
        "stage2_trade_line_no": idx + 1,
        "entry_signal_date": "2024-01-02",
        "entry_fill_date": "2024-01-03",
        "entry_date": "2024-01-03",
        "exit_date": "2024-01-10",
        "entry_price": 100.0,
        "exit_price": 110.0,
        "entry_signal_score": score,
        "entry_signal_threshold": threshold,
        "exit_reason": exit_reason,
    }
    return assign_canonical_lot_ids([row])[0]


def _histories() -> dict[str, pd.DataFrame]:
    idx = pd.date_range("2024-01-02", "2024-01-10", freq="B")
    return {
        "AAA": pd.DataFrame({"Open": [100, 101, 104, 108, 112, 116, 120], "Close": [100, 104, 108, 112, 116, 120, 124]}, index=idx),
        "BBB": pd.DataFrame({"Open": [100, 100, 101, 100, 99, 100, 101], "Close": [100, 101, 100, 99, 100, 101, 102]}, index=idx),
        "CCC": pd.DataFrame({"Open": [100, 99, 99, 98, 97, 96, 95], "Close": [100, 99, 98, 97, 96, 95, 94]}, index=idx),
        "DDD": pd.DataFrame({"Open": [100, 100, 100, 101, 101, 102, 102], "Close": [100, 100, 101, 101, 102, 102, 103]}, index=idx),
    }


def _selected_file(tmp_path: Path, trades: list[dict]) -> Path:
    p = tmp_path / "selected.jsonl"
    seen = set()
    rows = []
    for tr in trades:
        key = (tr["ticker"], tr["member_hash"], tr["rulebook_hash"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "ticker": tr["ticker"],
            "fold_label": "2024",
            "run_key": f"{tr['ticker']}|2024",
            "selected": {"member_hash": tr["member_hash"], "rulebook_hash": tr["rulebook_hash"], "rank": 1},
        })
    p.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    return p


def _trades_file(tmp_path: Path, trades: list[dict]) -> Path:
    p = tmp_path / "trades.jsonl"
    p.write_text("".join(json.dumps({k: v for k, v in tr.items() if k not in {"canonical_lot_id", "canonical_lot_group_key", "entry_sequence", "lot_id"}}) + "\n" for tr in trades), encoding="utf-8")
    return p


def _signal_lookup(trades: list[dict]) -> dict[tuple[str, str], dict]:
    out = {}
    for tr in trades:
        out[(tr["canonical_lot_id"], tr["entry_signal_date"])] = {
            "canonical_lot_id": tr["canonical_lot_id"],
            "decision_date": tr["entry_signal_date"],
            "ticker": tr["ticker"],
            "current_strength": tr["entry_signal_score"] / tr["entry_signal_threshold"],
            "strength_decay_pct": 0.0,
            "signal_valid": True,
            "use_llm_events": False,
        }
    return out


def test_signal_to_entry_weight_caps_at_20pct():
    cfg = LotProbeConfig(signal_to_weight_mode="aggressive_linear", max_entry_share_pct=20.0)
    assert signal_to_entry_weight(5.0, cfg) == 0.20
    assert 0.0 < signal_to_entry_weight(1.05, cfg) <= 0.20


def test_selected_loader_and_trade_filter_use_selected_pairs(tmp_path: Path):
    selected_trade = _trade("AAA", 0, 2.0)
    unselected_trade = _trade("ZZZ", 99, 2.0)
    selected_path = _selected_file(tmp_path, [selected_trade])
    trades_path = _trades_file(tmp_path, [selected_trade, unselected_trade])
    selected = load_selected_rulebook_set(selected_path)
    assert selected["selected_pair_count"] == 1
    rows = load_stage2_trade_candidates(trades_path, selected_jsonl=selected_path)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAA"
    assert rows[0]["canonical_lot_id"] == selected_trade["canonical_lot_id"]


def test_lot_probe_enforces_30pct_ticker_cap_with_multiple_lots():
    trades = [_trade("AAA", 0, 2.0), _trade("AAA", 1, 2.0), _trade("BBB", 2, 1.5), _trade("CCC", 3, 1.5), _trade("DDD", 4, 1.5)]
    metrics = simulate_lot_rebalance(trades, _histories(), LotProbeConfig(slippage_bps=0.0, signal_to_weight_mode="aggressive_linear"), signal_lookup=_signal_lookup(trades))
    assert metrics["max_ticker_gross_share_pct"] <= 30.0000001
    assert metrics["buy_count"] >= 4
    assert metrics["avg_gross_exposure_pct"] > 50.0


def test_slippage_reduces_return_vs_zero_slippage():
    trades = [_trade("AAA", 0, 2.0), _trade("BBB", 1, 1.5), _trade("DDD", 2, 1.5)]
    zero = simulate_lot_rebalance(trades, _histories(), LotProbeConfig(slippage_bps=0.0), signal_lookup=_signal_lookup(trades))
    slip = simulate_lot_rebalance(trades, _histories(), LotProbeConfig(slippage_bps=5.0), signal_lookup=_signal_lookup(trades))
    assert slip["slippage_cost_pct_initial_capital"] > 0
    assert slip["total_return_net_pct"] < zero["total_return_net_pct"]


def test_daily_signal_lookup_excludes_event_diagnostic(tmp_path: Path):
    p = tmp_path / "daily_signal_replay.jsonl"
    rows = [
        {"canonical_lot_id": "L1", "decision_date": "2024-01-02", "current_strength": 1.2, "strength_decay_pct": 0.0, "signal_valid": True, "use_llm_events": False},
        {"canonical_lot_id": "L2", "decision_date": "2024-01-02", "current_strength": 1.2, "strength_decay_pct": 0.0, "signal_valid": True, "use_llm_events": True},
    ]
    p.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    lookup = load_daily_signal_lookup(p)
    assert ("L1", "2024-01-02") in lookup
    assert ("L2", "2024-01-02") not in lookup


def test_dry_run_plan_detects_selected_and_join_gates(tmp_path: Path):
    trades = [_trade("AAA", 0, 2.0), _trade("BBB", 1, 1.5)]
    trades_path = _trades_file(tmp_path, trades)
    selected_path = _selected_file(tmp_path, trades)
    daily_path = tmp_path / "daily_signal_replay.jsonl"
    daily_rows = []
    for tr in trades:
        daily_rows.append({
            "canonical_lot_id": tr["canonical_lot_id"],
            "decision_date": tr["entry_signal_date"],
            "ticker": tr["ticker"],
            "current_strength": 1.2,
            "strength_decay_pct": 0.0,
            "signal_valid": True,
            "use_llm_events": False,
        })
    daily_path.write_text("".join(json.dumps(x) + "\n" for x in daily_rows), encoding="utf-8")
    summary = dry_run_plan(trades_path, tmp_path, selected_jsonl=selected_path, daily_signal_jsonl=daily_path)
    assert summary["selected_trade_rows"] == 2
    assert summary["duplicate_canonical_lot_id"] == 0
    assert summary["fail_gates"]["passed"] is True
    assert summary["fail_gates"]["join"]["join_success_rate_by_lot"] == 100.0
