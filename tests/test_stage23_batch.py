from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.research import run_stage23_batch as batch


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def out_dir_from_command(command: list[str]) -> Path:
    return Path(command[command.index("--out-dir") + 1])


def test_parse_ticker_file_normalizes_and_dedupes_preserving_order(tmp_path):
    tickers = tmp_path / "tickers.txt"
    tickers.write_text(" cw \n\nCRWD\ncw\n well\t\nCRWD\n", encoding="utf-8")

    assert batch.parse_ticker_file(tickers) == ["CW", "CRWD", "WELL"]


def test_missing_tickers_file_exits_before_subprocess(monkeypatch, tmp_path, capsys):
    missing = tmp_path / "missing_tickers.txt"

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called when --tickers is missing")

    monkeypatch.setattr(subprocess, "run", fail_run)

    with pytest.raises(SystemExit) as excinfo:
        batch.main([
            "--tickers",
            str(missing),
            "--out-root",
            str(tmp_path / "batch"),
            "--stage2",
            "--stage3-mode",
            "qualify-only",
        ])

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "tickers file not found:" in captured.err
    assert str(missing.resolve()) in captured.err
    assert not (tmp_path / "batch").exists()


def test_stage2_marker_requires_returncode_and_outputs(tmp_path):
    out_dir = tmp_path / "stage2"
    result = batch.make_stage2_marker("CW", out_dir, 0, 1.0, ["cmd"], tmp_path / "s2.log")
    assert result.status == "STAGE2_FAILED"
    assert not batch.stage2_done_marker(out_dir).exists()

    touch(out_dir / "summary.json")
    touch(out_dir / "rl_replay_trades.jsonl")
    touch(out_dir / "config.json")
    result = batch.make_stage2_marker("CW", out_dir, 0, 1.0, ["cmd"], tmp_path / "s2.log")

    assert result.status == "STAGE2_DONE"
    assert batch.stage2_done_marker(out_dir).exists()


def test_stage3_qualify_marker_classifies_rejected_as_terminal_success(tmp_path):
    out_dir = tmp_path / "stage3"
    write_json(out_dir / "qualify_result.json", {"ticker": "BAD", "qualified": False})

    result = batch.make_stage3_marker("BAD", out_dir, 0, 2.0, ["cmd"], tmp_path / "s3.log", "qualify-only")
    marker = json.loads(batch.stage3_done_marker(out_dir).read_text(encoding="utf-8"))

    assert result.status == "STAGE3_QUALIFY_REJECTED"
    assert result.qualified is False
    assert marker["status"] == "STAGE3_QUALIFY_REJECTED"
    assert marker["qualified"] is False


def test_stage3_full_marker_requires_full_outputs_and_all_summary(tmp_path):
    out_dir = tmp_path / "stage3"
    write_json(out_dir / "qualify_result.json", {"ticker": "CW", "qualified": True})
    touch(out_dir / "entry_result.json")
    touch(out_dir / "exit_result.json")
    touch(out_dir / "validate_result.json")
    touch(out_dir / "stage3_profile_catalog.jsonl")
    write_json(out_dir / "last_run_summary.json", {"stage": "qualify", "summaries": []})

    result = batch.make_stage3_marker("CW", out_dir, 0, 3.0, ["cmd"], tmp_path / "s3.log", "all")
    assert result.status == "STAGE3_FAILED"
    assert not batch.stage3_done_marker(out_dir).exists()

    write_json(out_dir / "last_run_summary.json", {"stage": "all", "summaries": [{}, {}, {}, {}]})
    result = batch.make_stage3_marker("CW", out_dir, 0, 3.0, ["cmd"], tmp_path / "s3.log", "all")
    assert result.status == "STAGE3_DONE"
    assert batch.stage3_done_marker(out_dir).exists()


def test_stale_outputs_without_marker_are_not_skipped(tmp_path):
    out_root = tmp_path / "batch"
    out_dir = out_root / "tickers" / "CW" / "stage3"
    write_json(out_dir / "qualify_result.json", {"ticker": "CW", "qualified": True})
    touch(out_dir / "entry_result.json")
    touch(out_dir / "exit_result.json")
    touch(out_dir / "validate_result.json")
    touch(out_dir / "stage3_profile_catalog.jsonl")
    write_json(out_dir / "last_run_summary.json", {"stage": "all", "summaries": [{}, {}, {}, {}]})

    result = batch.run_stage3_for_ticker(
        "CW",
        out_root,
        mode="all",
        skip_existing=True,
        retry_failed=False,
        dry_run=False,
    )

    assert result.status == "STAGE3_FAILED"
    assert "existing incomplete stage3 directory" in result.reason


def test_skip_existing_uses_marker_not_outputs(tmp_path):
    out_root = tmp_path / "batch"
    out_dir = out_root / "tickers" / "CW" / "stage3"
    write_json(out_dir / "_stage3_done.json", {"status": "STAGE3_DONE", "mode": "qualify-only", "qualified": True, "returncode": 0})

    result = batch.run_stage3_for_ticker(
        "CW",
        out_root,
        mode="qualify-only",
        skip_existing=True,
        retry_failed=False,
        dry_run=False,
    )

    assert result.status == "SKIPPED_EXISTING"
    assert result.marker_path == out_dir / "_stage3_done.json"


def test_dry_run_does_not_call_subprocess(monkeypatch, tmp_path):
    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called during dry-run")

    monkeypatch.setattr(subprocess, "run", fail_run)

    result = batch.run_stage3_for_ticker(
        "CW",
        tmp_path / "batch",
        mode="qualify-only",
        skip_existing=False,
        retry_failed=False,
        dry_run=True,
    )

    assert result.status == "PENDING"
    assert result.command is not None
    assert "--stage" in result.command
    assert Path(result.command[result.command.index("--out-dir") + 1]).is_absolute()


def test_subprocess_failure_is_recorded_and_next_ticker_continues(monkeypatch, tmp_path):
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("BAD\nGOOD\n", encoding="utf-8")
    calls = []

    def fake_run_subprocess(command, log_path):
        ticker = command[command.index("--ticker") + 1]
        calls.append(ticker)
        if ticker == "BAD":
            return 7, 0.1
        out_dir = out_dir_from_command(command)
        write_json(out_dir / "qualify_result.json", {"ticker": ticker, "qualified": True})
        return 0, 0.2

    monkeypatch.setattr(batch, "run_subprocess", fake_run_subprocess)

    rc = batch.main([
        "--tickers",
        str(ticker_file),
        "--out-root",
        str(tmp_path / "batch"),
        "--stage3-mode",
        "qualify-only",
    ])
    rows = [json.loads(line) for line in (tmp_path / "batch" / "batch_index.jsonl").read_text(encoding="utf-8").splitlines()]
    statuses = {row["ticker"]: row["status"] for row in rows}

    assert rc == 0
    assert calls == ["BAD", "GOOD"]
    assert statuses == {"BAD": "STAGE3_FAILED", "GOOD": "STAGE3_DONE"}


def test_qualify_rejection_marker_created_and_batch_continues(monkeypatch, tmp_path):
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("REJECT\nPASS\n", encoding="utf-8")

    def fake_run_subprocess(command, log_path):
        ticker = command[command.index("--ticker") + 1]
        out_dir = out_dir_from_command(command)
        write_json(out_dir / "qualify_result.json", {"ticker": ticker, "qualified": ticker == "PASS"})
        return 0, 0.1

    monkeypatch.setattr(batch, "run_subprocess", fake_run_subprocess)

    rc = batch.main([
        "--tickers",
        str(ticker_file),
        "--out-root",
        str(tmp_path / "batch"),
        "--stage3-mode",
        "qualify-only",
    ])
    rows = [json.loads(line) for line in (tmp_path / "batch" / "batch_index.jsonl").read_text(encoding="utf-8").splitlines()]
    statuses = {row["ticker"]: row["status"] for row in rows}

    assert rc == 0
    assert statuses["REJECT"] == "STAGE3_QUALIFY_REJECTED"
    assert statuses["PASS"] == "STAGE3_DONE"
    assert (tmp_path / "batch" / "tickers" / "REJECT" / "stage3" / "_stage3_done.json").exists()
    assert (tmp_path / "batch" / "tickers" / "PASS" / "stage3" / "_stage3_done.json").exists()


def test_stage3_all_qualify_rejection_is_terminal_success(tmp_path):
    out_dir = tmp_path / "stage3"
    write_json(out_dir / "qualify_result.json", {"ticker": "REJECT", "qualified": False})

    result = batch.make_stage3_marker("REJECT", out_dir, 0, 1.0, ["cmd"], tmp_path / "s3.log", "all")
    marker = json.loads(batch.stage3_done_marker(out_dir).read_text(encoding="utf-8"))

    assert result.status == "STAGE3_QUALIFY_REJECTED"
    assert marker["mode"] == "all"
    assert marker["validated_outputs"] == ["qualify_result.json"]


def test_main_dry_run_records_plan_without_subprocess(monkeypatch, tmp_path, capsys):
    ticker_file = tmp_path / "tickers.txt"
    ticker_file.write_text("CW\nCRWD\n", encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called during dry-run")

    monkeypatch.setattr(subprocess, "run", fail_run)

    rc = batch.main([
        "--tickers", str(ticker_file),
        "--out-root", str(tmp_path / "batch"),
        "--stage2",
        "--stage3-mode", "qualify-only",
        "--dry-run",
    ])
    captured = capsys.readouterr().out
    rows = [json.loads(line) for line in (tmp_path / "batch" / "batch_index.jsonl").read_text(encoding="utf-8").splitlines()]

    assert rc == 0
    assert "DRY-RUN STAGE2 CW" in captured
    assert "DRY-RUN STAGE3 CRWD" in captured
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"PENDING"}
