from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.live.live_exit_dry_run_rehearsal import run_exit_rehearsal


def test_live_exit_dry_run_rehearsal_all_exit_reasons_and_realized_pnl(tmp_path):
    result = run_exit_rehearsal(artifact_dir=tmp_path)

    assert result.ok, result.scenarios
    assert result.scenario_count == 6
    assert result.passed == 6
    assert result.failed == 0

    by_name = {row["scenario"]: row for row in result.scenarios}
    assert set(by_name) == {"stop_loss", "take_profit", "trailing", "breakeven_stop", "sell_omen", "time_out"}

    for name, row in by_name.items():
        assert row["ok"], row
        assert row["exit_reason"] == row["expected_reason"]
        assert row["trade_log_written"] is True
        assert row["position_removed"] is True
        assert row["exit_notification_sent"] is True
        assert row["broker_orders"] == 2
        assert row["orders_attempted_before_exit"] == 1
        assert row["orders_filled_before_exit"] == 1
        assert row["orders_attempted_after_exit"] == 1
        assert row["orders_filled_after_exit"] == 1
        assert abs(float(row["realized_pnl_today"]) - float(row["pnl_krw"])) < 1e-6

    assert by_name["stop_loss"]["pnl_krw"] < 0
    assert by_name["stop_loss"]["consecutive_losses"] == 1
    for name in ["take_profit", "trailing", "breakeven_stop", "sell_omen", "time_out"]:
        assert by_name[name]["pnl_krw"] > 0
        assert by_name[name]["consecutive_losses"] == 0

    assert (tmp_path / "exit_rehearsal_result.json").exists()
    for name in by_name:
        assert (tmp_path / name / "scenario_result.json").exists()
        assert (tmp_path / name / "trade_log.csv").exists()
