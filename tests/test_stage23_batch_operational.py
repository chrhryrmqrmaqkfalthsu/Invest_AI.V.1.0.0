from __future__ import annotations

import json
from pathlib import Path

from scripts.research import run_stage23_batch as batch
from scripts.research import run_stage3_aggressive as stage3


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_retry_failed_uses_retry_dir_without_overwriting_incomplete_canonical(tmp_path):
    ticker_root = tmp_path / "tickers" / "CW"
    canonical = ticker_root / "stage2"
    canonical.mkdir(parents=True)
    (canonical / "partial.txt").write_text("keep", encoding="utf-8")

    out_dir, reason = batch.choose_run_dir(ticker_root, "stage2", retry_failed=True)

    assert reason is None
    assert out_dir == ticker_root / "stage2_retry1"
    assert (canonical / "partial.txt").read_text(encoding="utf-8") == "keep"


def test_central_index_append_is_append_only(tmp_path):
    first = {"event_type": "stage2_survivor", "ticker": "A", "rulebook_hash": "h1"}
    second = {"event_type": "stage3_profile_catalog", "ticker": "A", "rulebook_hash": "h2"}

    assert batch.append_central_index(tmp_path, [first]) == 1
    assert batch.append_central_index(tmp_path, [second]) == 1

    rows = [json.loads(line) for line in (tmp_path / batch.CENTRAL_INDEX_NAME).read_text(encoding="utf-8").splitlines()]
    assert rows == [first, second]


def test_notification_events_record_start_send_and_progress(monkeypatch, tmp_path):
    class FakeTelegramNotifier:
        def __init__(self, *args, **kwargs):
            self.enabled = True

        def send(self, text, parse_mode=""):
            return True

        def send_progress(self, text):
            return 77

        def edit_message(self, message_id, text, parse_mode=""):
            return True

    monkeypatch.setattr(batch, "TelegramNotifier", FakeTelegramNotifier)
    notifier = batch.BatchProgressNotifier(run_id="notify_test", out_root=tmp_path, total_tickers=2, total_events=4)
    args = type(
        "Args",
        (),
        {
            "stage2": True,
            "stage3_mode": "all",
            "max_workers_stage2": 1,
            "max_workers_stage3": 1,
        },
    )()
    disk = batch.DiskState(level="OK", path=str(tmp_path), total_bytes=100 * batch.GB, used_bytes=1 * batch.GB, free_bytes=99 * batch.GB, used_pct=1.0)

    notifier.start(args, disk)

    rows = [json.loads(line) for line in (tmp_path / batch.NOTIFICATION_EVENTS_NAME).read_text(encoding="utf-8").splitlines()]
    event_types = [row["event_type"] for row in rows]
    assert "notifier_init_begin" in event_types
    assert "notifier_init_done" in event_types
    assert "start_called" in event_types
    assert any(row["event_type"] == "send" and row["result"] is True for row in rows)
    assert any(row["event_type"] == "send_progress" and row["result"] is True and row["message_id"] == 77 for row in rows)
    assert all("token" not in row and "chat_id" not in row for row in rows)


def test_stage2_central_index_rows_include_survivor_metrics_and_paths(tmp_path):
    out_root = tmp_path / "batch"
    out_dir = out_root / "tickers" / "CW" / "stage2"
    write_jsonl(
        out_dir / "survivors.jsonl",
        [
            {
                "ticker": "CW",
                "rulebook_hash": "stage2hash",
                "origin_train_labels": ["train_1"],
                "origin_count": 1,
                "periods": [
                    {
                        "period_label": "oos_2025h2",
                        "expectancy_pct": 1.2,
                        "max_drawdown_pct": -4.0,
                        "profit_factor": 2.5,
                        "trade_count": 9,
                    }
                ],
            }
        ],
    )
    args = type("Args", (), {"run_id": "run1"})()
    result = batch.StageRunResult("STAGE2_DONE", out_dir)

    rows = batch.build_stage2_central_index_rows(args, out_root, result, "CW")

    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "stage2_survivor"
    assert row["rulebook_hash"] == "stage2hash"
    assert row["metrics"]["oos_2025h2"]["expectancy_pct"] == 1.2
    assert row["artifact_paths"]["survivors"].endswith("survivors.jsonl")


def test_stage3_central_index_rows_include_profile_identity(tmp_path):
    out_root = tmp_path / "batch"
    out_dir = out_root / "tickers" / "CW" / "stage3"
    write_jsonl(
        out_dir / "stage3_profile_catalog.jsonl",
        [
            {
                "ticker": "CW",
                "rank": 1,
                "rulebook_hash": "finalhash",
                "entry_rulebook_hash": "entryhash",
                "entry_rank": 7,
                "exit_rank": 2,
                "eligible_stage3_basic": True,
                "holding_class": "mid",
                "risk_class": "low_mdd",
                "return_class": "mid_exp",
                "composite_tag": "mid|low_mdd|mid_exp",
                "per_period_metrics": {"recent_1y": {"expectancy_pct": 3.0, "trade_count": 12}},
            }
        ],
    )
    args = type("Args", (), {"run_id": "run1"})()
    result = batch.StageRunResult("STAGE3_DONE", out_dir)

    rows = batch.build_stage3_central_index_rows(args, out_root, result, "CW")

    profile_rows = [row for row in rows if row["event_type"] == "stage3_profile_catalog"]
    assert len(profile_rows) == 1
    row = profile_rows[0]
    assert row["rulebook_hash"] == "finalhash"
    assert row["entry_rulebook_hash"] == "entryhash"
    assert row["entry_rank"] == 7
    assert row["exit_rank"] == 2
    assert row["profile"]["composite_tag"] == "mid|low_mdd|mid_exp"


def test_disk_threshold_classification_uses_approved_levels():
    assert batch.classify_disk_level(51 * batch.GB, 30.0) == "OK"
    assert batch.classify_disk_level(49 * batch.GB, 30.0) == "WARN"
    assert batch.classify_disk_level(19 * batch.GB, 30.0) == "CRITICAL"
    assert batch.classify_disk_level(9 * batch.GB, 30.0) == "FATAL"
    assert batch.classify_disk_level(60 * batch.GB, 90.0) == "CRITICAL"


def test_preserve_legacy_validated_survivors_renames_without_deleting(tmp_path):
    legacy = tmp_path / "validated_survivors.jsonl"
    deprecated = tmp_path / "validated_survivors.jsonl.deprecated"
    legacy.write_text('{"ticker":"CW"}\n', encoding="utf-8")

    stage3.preserve_legacy_validated_survivors(tmp_path)

    assert not legacy.exists()
    assert deprecated.read_text(encoding="utf-8") == '{"ticker":"CW"}\n'


def test_preserve_legacy_validated_survivors_keeps_original_when_deprecated_exists(tmp_path):
    legacy = tmp_path / "validated_survivors.jsonl"
    deprecated = tmp_path / "validated_survivors.jsonl.deprecated"
    legacy.write_text("new\n", encoding="utf-8")
    deprecated.write_text("old\n", encoding="utf-8")

    stage3.preserve_legacy_validated_survivors(tmp_path)

    assert legacy.read_text(encoding="utf-8") == "new\n"
    assert deprecated.read_text(encoding="utf-8") == "old\n"
