import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from engine.central.parameters_adapter import (
    ParametersAdapterError,
    build_parameters_from_stage3_row,
    join_stage3_rows_by_rulebook_hash,
    join_stats_by_rulebook_hash,
    load_asset_meta_for_ticker,
    load_final_rulebooks,
    load_stage3_catalog,
    write_parameters,
)
from engine.core.metadata import compute_rulebook_hash


CATALOG_PATH = Path("exp_cw_stage3_20260613_0001/stage3_profile_catalog.jsonl")
FINAL_PATH = Path("exp_cw_stage3_20260613_0001/final_rulebooks.jsonl")
CREATED_AT = "2026-06-19T00:00:00Z"
PROMOTION_ID = "stage3_test_promotion"
VERSION = "stage3_parameters_adapter_v1"


def _sample_rows():
    catalog = load_stage3_catalog(CATALOG_PATH)
    final = load_final_rulebooks(FINAL_PATH)
    rows = join_stage3_rows_by_rulebook_hash(catalog, final)
    assert rows
    return rows[0]


def _asset_meta():
    return load_asset_meta_for_ticker("CW")


def _build(row=None, asset_meta=None, **kwargs):
    return build_parameters_from_stage3_row(
        row or _sample_rows(),
        asset_meta=asset_meta if asset_meta is not None else _asset_meta(),
        promotion_id=kwargs.pop("promotion_id", PROMOTION_ID),
        source_run_dir=kwargs.pop("source_run_dir", "exp_cw_stage3_20260613_0001"),
        source_run_id=kwargs.pop("source_run_id", "exp_cw_stage3_20260613_0001"),
        version=kwargs.pop("version", VERSION),
        created_at=kwargs.pop("created_at", CREATED_AT),
    )


def test_loaders_and_join_intersection_stats():
    catalog = load_stage3_catalog(CATALOG_PATH)
    final = load_final_rulebooks(FINAL_PATH)
    stats = join_stats_by_rulebook_hash(catalog, final)
    joined = join_stage3_rows_by_rulebook_hash(catalog, final)
    assert stats.catalog_rows == 60
    assert stats.final_rows == 60
    assert stats.joined_rows == 60
    assert stats.catalog_only == 0
    assert stats.final_only == 0
    assert len(joined) == 60
    assert "period_results" in joined[0]
    assert "bull_metrics" in joined[0]
    assert "stress_metrics" in joined[0]


def test_join_excludes_one_sided_hashes_and_reports_counters():
    catalog = [{"rulebook_hash": "a", "rulebook": {}}, {"rulebook_hash": "b", "rulebook": {}}]
    final = [{"rulebook_hash": "b", "bull_metrics": {}}, {"rulebook_hash": "c", "bull_metrics": {}}]
    stats = join_stats_by_rulebook_hash(catalog, final)
    joined = join_stage3_rows_by_rulebook_hash(catalog, final)
    assert stats.catalog_rows == 2
    assert stats.final_rows == 2
    assert stats.joined_rows == 1
    assert stats.catalog_only == 1
    assert stats.final_only == 1
    assert stats.catalog_only_hashes == ("a",)
    assert stats.final_only_hashes == ("c",)
    assert [r["rulebook_hash"] for r in joined] == ["b"]


def test_build_parameters_from_stage3_row_success_shape_and_validation():
    row = _sample_rows()
    payload = _build(row)
    assert sorted(payload.keys()) == ["asset_meta", "promotion", "rulebook", "saved_at", "version"]
    assert payload["version"] == VERSION
    assert payload["saved_at"] == CREATED_AT
    assert payload["asset_meta"]["ticker"] == "CW"
    assert payload["promotion"]["promotion_id"] == PROMOTION_ID
    assert payload["promotion"]["rulebook_hash"] == row["rulebook_hash"]
    assert payload["promotion"]["member_hash"] == row["rulebook_hash"]
    assert payload["promotion"]["source"] == "stage3_profile_catalog"
    assert payload["promotion"]["source_run_dir"] == "exp_cw_stage3_20260613_0001"
    assert payload["promotion"]["source_run_id"] == "exp_cw_stage3_20260613_0001"
    assert payload["rulebook"] == row["rulebook"]
    assert len(payload["rulebook"]) == 88


def test_selected_member_and_selection_structures_exist():
    payload = _build()
    selected = payload["promotion"]["selected_member"]
    selection = payload["promotion"]["selection"]
    assert selected["rulebook_hash"] == payload["promotion"]["rulebook_hash"]
    assert "pure_oos_metrics" in selected
    assert "stress_reference_metrics" in selected
    assert "period_results" in selection
    assert "profile_config" in selection
    assert payload["promotion"]["selection_filter"] == {"source": "stage3", "version": VERSION}


def test_created_at_fixed_makes_output_deterministic():
    row = _sample_rows()
    asset_meta = _asset_meta()
    first = _build(row, asset_meta=asset_meta, created_at=CREATED_AT)
    second = _build(row, asset_meta=asset_meta, created_at=CREATED_AT)
    assert first == second


def test_rulebook_hash_mutation_fails_guard():
    row = copy.deepcopy(_sample_rows())
    row["rulebook"]["signal_threshold"] = float(row["rulebook"].get("signal_threshold", 0.0)) + 0.12345
    with pytest.raises(ParametersAdapterError, match="rulebook_hash mismatch"):
        _build(row)


def test_row_rulebook_hash_mismatch_fails_guard():
    row = copy.deepcopy(_sample_rows())
    row["rulebook_hash"] = "0" * 64
    with pytest.raises(ParametersAdapterError, match="rulebook_hash mismatch"):
        _build(row)


def test_ticker_mismatch_fails_guard():
    row = copy.deepcopy(_sample_rows())
    row["ticker"] = "WELL"
    with pytest.raises(ParametersAdapterError, match="ticker mismatch"):
        _build(row)


def test_rulebook_ticker_mismatch_fails_guard():
    row = copy.deepcopy(_sample_rows())
    row["rulebook"]["ticker"] = "WELL"
    row["rulebook_hash"] = compute_rulebook_hash(row["rulebook"])
    with pytest.raises(ParametersAdapterError, match="ticker mismatch"):
        _build(row)


def test_direction_not_long_fails_guard():
    row = copy.deepcopy(_sample_rows())
    row["rulebook"]["direction"] = "short"
    row["rulebook_hash"] = compute_rulebook_hash(row["rulebook"])
    with pytest.raises(ParametersAdapterError, match="direction must be long"):
        _build(row)


def test_missing_asset_meta_fails_closed():
    with pytest.raises(ParametersAdapterError, match="asset_meta missing"):
        _build(asset_meta={})


def test_load_asset_meta_for_missing_ticker_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_asset_meta_for_ticker("NOPE", symbols_dir=tmp_path)


def test_empty_promotion_id_fails_guard():
    with pytest.raises(ParametersAdapterError, match="promotion_id must be non-empty"):
        _build(promotion_id="")


def test_empty_version_fails_guard():
    with pytest.raises(ParametersAdapterError, match="version must be non-empty"):
        _build(version="")


def test_asset_meta_ticker_is_corrected_to_output_ticker():
    asset = _asset_meta()
    asset["ticker"] = "WRONG"
    payload = _build(asset_meta=asset)
    assert payload["asset_meta"]["ticker"] == "CW"


def test_live_validation_catches_market_mismatch():
    asset = _asset_meta()
    asset["currency"] = "KRW"
    with pytest.raises(ParametersAdapterError, match="live universe validation failed"):
        _build(asset_meta=asset)


def test_loader_rejects_non_object_jsonl_row(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ParametersAdapterError, match="row must be object"):
        load_stage3_catalog(path)


def test_load_asset_meta_returns_copy_not_source_mutation():
    asset = load_asset_meta_for_ticker("CW")
    asset["ticker"] = "MUTATED"
    fresh = load_asset_meta_for_ticker("CW")
    assert fresh["ticker"] == "CW"


def test_dry_run_payload_can_be_json_serialized():
    payload = _build()
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert '"promotion_id": "stage3_test_promotion"' in text


def test_write_parameters_dry_run_does_not_create_file(tmp_path):
    out_path = tmp_path / "CW" / "parameters.json"
    report = write_parameters(_build(), out_path)
    assert report["dry_run"] is True
    assert report["written"] is False
    assert report["skipped"] is True
    assert report["out_path"] == str(out_path)
    assert not out_path.exists()


def test_write_parameters_actual_write_creates_valid_json(tmp_path):
    out_path = tmp_path / "CW" / "parameters.json"
    report = write_parameters(_build(), out_path, dry_run=False)
    assert report["written"] is True
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert sorted(payload.keys()) == ["asset_meta", "promotion", "rulebook", "saved_at", "version"]
    assert payload["asset_meta"]["ticker"] == "CW"
    assert payload["promotion"]["promotion_id"] == PROMOTION_ID


def test_write_parameters_backup_existing_file(tmp_path):
    out_path = tmp_path / "CW" / "parameters.json"
    out_path.parent.mkdir(parents=True)
    out_path.write_text('{"old": true}\n', encoding="utf-8")
    report = write_parameters(_build(), out_path, dry_run=False, backup=True)
    assert report["written"] is True
    backup_path = Path(report["backup_path"])
    assert backup_path.exists()
    assert json.loads(backup_path.read_text(encoding="utf-8")) == {"old": True}
    assert json.loads(out_path.read_text(encoding="utf-8"))["asset_meta"]["ticker"] == "CW"


def test_write_parameters_atomic_failure_keeps_existing_file_and_cleans_tmp(tmp_path, monkeypatch):
    out_path = tmp_path / "CW" / "parameters.json"
    out_path.parent.mkdir(parents=True)
    out_path.write_text('{"old": true}\n', encoding="utf-8")

    def boom(src, dst):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError, match="replace failed"):
        write_parameters(_build(), out_path, dry_run=False, backup=False)
    assert json.loads(out_path.read_text(encoding="utf-8")) == {"old": True}
    assert list(out_path.parent.glob(".parameters.json.tmp.*")) == []


def test_write_parameters_refuses_live_symbols_path_without_explicit_allow():
    with pytest.raises(ParametersAdapterError, match="refusing to write live symbols path"):
        write_parameters(_build(), Path("data/symbols/CW/parameters.json"), dry_run=True)


def test_write_parameters_live_symbols_path_forces_backup_when_allowed():
    report = write_parameters(
        _build(),
        Path("data/symbols/CW/parameters.json"),
        dry_run=True,
        backup=False,
        allow_live_symbols=True,
    )
    assert report["live_symbols_path"] is True
    assert report["backup"] is True
    assert report["backup_forced"] is True
    assert report["written"] is False


def test_write_parameters_revalidates_and_rejects_invalid_payload(tmp_path):
    payload = _build()
    payload["rulebook"]["ticker"] = "WRONG"
    with pytest.raises(ParametersAdapterError, match="live universe validation failed"):
        write_parameters(payload, tmp_path / "CW" / "parameters.json", dry_run=False)
    assert not (tmp_path / "CW" / "parameters.json").exists()


def test_cli_dry_run_outputs_report_and_does_not_write(tmp_path):
    out_dir = tmp_path / "out"
    proc = _run_cli(out_dir)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "dry_run"
    assert payload["write_report"]["written"] is False
    assert payload["payload_summary"]["rulebook_key_count"] == 88
    assert not (out_dir / "CW" / "parameters.json").exists()


def test_cli_write_to_tmp_out_dir_creates_file(tmp_path):
    out_dir = tmp_path / "out"
    proc = _run_cli(out_dir, "--write")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "write"
    out_path = out_dir / "CW" / "parameters.json"
    assert out_path.exists()
    written = json.loads(out_path.read_text(encoding="utf-8"))
    assert written["promotion"]["promotion_id"] == PROMOTION_ID


def _run_cli(out_dir: Path, *extra_args: str):
    args = [
        sys.executable,
        "-m",
        "engine.central.parameters_cli",
        "--catalog",
        str(CATALOG_PATH),
        "--final",
        str(FINAL_PATH),
        "--ticker",
        "CW",
        "--rank",
        "1",
        "--promotion-id",
        PROMOTION_ID,
        "--version",
        VERSION,
        "--source-run-dir",
        "exp_cw_stage3_20260613_0001",
        "--source-run-id",
        "exp_cw_stage3_20260613_0001",
        "--out-dir",
        str(out_dir),
    ]
    args.extend(extra_args)
    return subprocess.run(args, text=True, capture_output=True, check=False)
