import json
from pathlib import Path

import pytest

from engine.live import elite_shadow_trader as trader


def test_load_state_fail_closed_on_corrupt_json(tmp_path, monkeypatch):
    state_path = tmp_path / "elite_shadow_state.json"
    state_path.write_text("{ broken json", encoding="utf-8")
    monkeypatch.setattr(trader, "STATE_PATH", state_path)

    with pytest.raises(trader.ShadowStateCorruptionError):
        trader.load_state()

    corrupt_files = list(tmp_path.glob("elite_shadow_state.json.corrupt.*"))
    assert corrupt_files, "corrupt backup file should be created"
    assert state_path.read_text(encoding="utf-8") == "{ broken json"


def test_shadow_state_lock_blocks_second_writer(tmp_path, monkeypatch):
    lock_path = tmp_path / "elite_shadow_tick.lock"
    monkeypatch.setattr(trader, "LOCK_PATH", lock_path)

    assert trader._acquire_lock()
    try:
        assert not trader._acquire_lock()
    finally:
        trader._release_lock()
    assert not lock_path.exists()


def test_append_trade_uses_trade_lock_and_writes_jsonl(tmp_path, monkeypatch):
    trades_path = tmp_path / "elite_shadow_trades.jsonl"
    trade_lock_path = tmp_path / "elite_shadow_trades.lock"
    monkeypatch.setattr(trader, "TRADES_PATH", trades_path)
    monkeypatch.setattr(trader, "TRADE_LOCK_PATH", trade_lock_path)

    trader.append_trade({"ticker": "TEST", "pnl_pct": 1.23})

    rows = [json.loads(line) for line in trades_path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"pnl_pct": 1.23, "ticker": "TEST"}]
    assert not trade_lock_path.exists()


def test_append_trade_fails_closed_when_trade_lock_busy(tmp_path, monkeypatch):
    trades_path = tmp_path / "elite_shadow_trades.jsonl"
    trade_lock_path = tmp_path / "elite_shadow_trades.lock"
    trade_lock_path.write_text("busy", encoding="utf-8")
    monkeypatch.setattr(trader, "TRADES_PATH", trades_path)
    monkeypatch.setattr(trader, "TRADE_LOCK_PATH", trade_lock_path)

    with pytest.raises(RuntimeError):
        trader.append_trade({"ticker": "TEST"})

    assert not trades_path.exists()
