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
