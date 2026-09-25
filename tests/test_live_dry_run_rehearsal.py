from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.live.live_dry_run_rehearsal import run_rehearsal


def test_live_dry_run_rehearsal_buy_fill_position_notification(tmp_path):
    result = run_rehearsal(
        ticker="AAPL",
        price=100.0,
        initial_cash=100_000.0,
        order_notional=30.0,
        artifact_dir=tmp_path,
        manual_sell_intent_path=tmp_path / "manual_sell_intent.json",
    )

    assert result.ok, result.errors
    assert result.signals_buy == 1
    assert result.orders_attempted == 1
    assert result.orders_filled == 1
    assert result.broker_orders == 1
    assert result.broker_holdings == 1
    assert result.notifier_events >= 3
    assert result.positions_saved is True
    assert (tmp_path / "positions.json").exists()
    assert (tmp_path / "rehearsal_result.json").exists()
    assert (tmp_path / "notifier_events.json").exists()
