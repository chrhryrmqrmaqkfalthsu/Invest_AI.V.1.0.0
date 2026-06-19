"""Stage3 row to live ``parameters.json`` payload adapter.

The adapter builds validated dictionaries and can write them to a non-live output
area with atomic replacement. Writing to ``data/symbols`` is blocked unless the
caller explicitly opts in.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from engine.core.metadata import compute_member_hash, compute_rulebook_hash
from engine.live.universe import LiveUniverseError, _validate_parameters


class ParametersAdapterError(ValueError):
    """Raised when a Stage3 row cannot be safely converted."""


@dataclass(frozen=True)
class JoinStats:
    catalog_rows: int
    final_rows: int
    joined_rows: int
    catalog_only: int
    final_only: int
    catalog_only_hashes: tuple[str, ...]
    final_only_hashes: tuple[str, ...]


def load_stage3_catalog(path: str | Path) -> list[dict[str, Any]]:
    return _load_jsonl(path)


def load_final_rulebooks(path: str | Path) -> list[dict[str, Any]]:
    return _load_jsonl(path)


def join_stats_by_rulebook_hash(catalog_rows: Iterable[Mapping[str, Any]], final_rows: Iterable[Mapping[str, Any]]) -> JoinStats:
    catalog_list = [dict(row) for row in catalog_rows]
    final_list = [dict(row) for row in final_rows]
    catalog_hashes = set(_row_hash(row) for row in catalog_list if _row_hash(row))
    final_hashes = set(_row_hash(row) for row in final_list if _row_hash(row))
    catalog_only = tuple(sorted(catalog_hashes - final_hashes))
    final_only = tuple(sorted(final_hashes - catalog_hashes))
    return JoinStats(
        catalog_rows=len(catalog_list),
        final_rows=len(final_list),
        joined_rows=len(catalog_hashes & final_hashes),
        catalog_only=len(catalog_only),
        final_only=len(final_only),
        catalog_only_hashes=catalog_only,
        final_only_hashes=final_only,
    )


def join_stage3_rows_by_rulebook_hash(catalog_rows: Iterable[Mapping[str, Any]], final_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return merged rows for the rulebook-hash intersection only.

    Catalog fields are the base because they carry period_results and profile
    metadata. Final-rulebook fields are added where non-conflicting; conflicting
    unequal final values are preserved under ``final_<key>``.
    """
    catalog_by_hash = _unique_by_hash(catalog_rows, label="catalog")
    final_by_hash = _unique_by_hash(final_rows, label="final")
    joined: list[dict[str, Any]] = []
    for h in sorted(set(catalog_by_hash) & set(final_by_hash)):
        row = copy.deepcopy(catalog_by_hash[h])
        final = final_by_hash[h]
        for key, value in final.items():
            if key not in row:
                row[key] = copy.deepcopy(value)
            elif row[key] == value:
                continue
            else:
                row[f"final_{key}"] = copy.deepcopy(value)
        joined.append(row)
    return joined


def load_asset_meta_for_ticker(ticker: str, symbols_dir: str | Path = "data/symbols") -> dict[str, Any]:
    ticker_u = _normalize_ticker(ticker)
    path = Path(symbols_dir) / ticker_u / "parameters.json"
    if not path.exists():
        raise FileNotFoundError(f"asset_meta source parameters missing for {ticker_u}: {path}")
    payload = _read_json_object(path)
    asset_meta = payload.get("asset_meta")
    if not isinstance(asset_meta, Mapping) or not asset_meta:
        raise ParametersAdapterError(f"asset_meta missing or invalid for {ticker_u}: {path}")
    return copy.deepcopy(dict(asset_meta))


def build_parameters_from_stage3_row(
    row: Mapping[str, Any],
    *,
    asset_meta: Mapping[str, Any],
    promotion_id: str,
    source_run_dir: str,
    source_run_id: str,
    version: str,
    created_at: str | datetime | None = None,
) -> dict[str, Any]:
    row_dict = dict(row or {})
    version_s = _require_non_empty(version, "version")
    promotion_id_s = _require_non_empty(promotion_id, "promotion_id")
    created = _utc_iso(created_at)
    ticker = _validate_stage3_row(row_dict)
    asset = _prepare_asset_meta(asset_meta, ticker)
    rulebook = copy.deepcopy(dict(row_dict["rulebook"]))
    rulebook_hash = str(row_dict["rulebook_hash"]).strip()
    member_hash = compute_member_hash(rulebook)
    payload = {
        "asset_meta": asset,
        "promotion": {
            "created_at": created,
            "member_hash": member_hash,
            "promotion_id": promotion_id_s,
            "rulebook_hash": rulebook_hash,
            "selected_member": _build_selected_member(row_dict),
            "selection": _build_selection(row_dict),
            "selection_filter": {
                "source": "stage3",
                "version": version_s,
            },
            "source": "stage3_profile_catalog",
            "source_run_dir": str(source_run_dir or ""),
            "source_run_id": str(source_run_id or ""),
        },
        "rulebook": rulebook,
        "saved_at": created,
        "version": version_s,
    }
    _dry_validate_live_payload(ticker, payload)
    return payload


def write_parameters(
    params: Mapping[str, Any],
    out_path: str | Path,
    *,
    dry_run: bool = True,
    backup: bool = True,
    allow_live_symbols: bool = False,
) -> dict[str, Any]:
    """Validate and optionally write a parameters payload atomically.

    ``dry_run`` is the default and never creates directories or files. Actual
    writes use a same-directory temporary file followed by ``os.replace``. If
    the target already exists and backup is enabled, a timestamped ``.bak`` copy
    is created before replacement.
    """
    payload = copy.deepcopy(dict(params or {}))
    ticker = _payload_ticker(payload)
    _dry_validate_live_payload(ticker, payload)
    path = Path(out_path)
    live_symbols_path = _is_under_live_symbols(path)
    backup_forced = False
    if live_symbols_path:
        if not allow_live_symbols:
            raise ParametersAdapterError(f"refusing to write live symbols path without allow_live_symbols=True: {path}")
        if not backup:
            backup_forced = True
            backup = True
    text = _json_dumps(payload)
    report = {
        "dry_run": bool(dry_run),
        "written": False,
        "skipped": bool(dry_run),
        "out_path": str(path),
        "backup": bool(backup),
        "backup_forced": bool(backup_forced),
        "backup_path": None,
        "live_symbols_path": bool(live_symbols_path),
        "ticker": ticker,
        "promotion_id": str((payload.get("promotion") or {}).get("promotion_id") or ""),
        "rulebook_hash": str((payload.get("promotion") or {}).get("rulebook_hash") or ""),
        "bytes": len(text.encode("utf-8")),
    }
    if dry_run:
        return report

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and backup:
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)
        report["backup_path"] = str(backup_path)
    tmp_path = _tmp_path(path)
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        finally:
            pass
        raise
    report["written"] = True
    report["skipped"] = False
    return report


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"JSONL file missing: {p}")
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception as exc:
            raise ParametersAdapterError(f"invalid JSONL at {p}:{lineno}: {exc}") from exc
        if not isinstance(row, dict):
            raise ParametersAdapterError(f"JSONL row must be object at {p}:{lineno}")
        rows.append(row)
    return rows


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ParametersAdapterError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ParametersAdapterError(f"JSON root must be object: {path}")
    return payload


def _unique_by_hash(rows: Iterable[Mapping[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for idx, row in enumerate(rows, start=1):
        h = _row_hash(row)
        if not h:
            raise ParametersAdapterError(f"{label} row {idx}: rulebook_hash missing")
        if h in out:
            raise ParametersAdapterError(f"{label} duplicate rulebook_hash: {h}")
        out[h] = copy.deepcopy(dict(row))
    return out


def _row_hash(row: Mapping[str, Any]) -> str:
    return str((row or {}).get("rulebook_hash") or "").strip()


def _validate_stage3_row(row: Mapping[str, Any]) -> str:
    rulebook = row.get("rulebook")
    if not isinstance(rulebook, Mapping) or not rulebook:
        raise ParametersAdapterError("stage3 row rulebook missing or invalid")
    row_hash = str(row.get("rulebook_hash") or "").strip()
    if not row_hash:
        raise ParametersAdapterError("stage3 row rulebook_hash missing")
    computed_rulebook_hash = compute_rulebook_hash(rulebook)
    computed_member_hash = compute_member_hash(rulebook)
    if computed_rulebook_hash != row_hash:
        raise ParametersAdapterError(f"rulebook_hash mismatch: row={row_hash} computed={computed_rulebook_hash}")
    if computed_member_hash != row_hash:
        raise ParametersAdapterError(f"member_hash mismatch: row={row_hash} computed={computed_member_hash}")
    row_ticker = _normalize_ticker(row.get("ticker"))
    rb_ticker = _normalize_ticker(rulebook.get("ticker"))
    if not row_ticker or not rb_ticker or row_ticker != rb_ticker:
        raise ParametersAdapterError(f"ticker mismatch: row={row_ticker!r} rulebook={rb_ticker!r}")
    direction = str(rulebook.get("direction") or "").strip().lower()
    if direction != "long":
        raise ParametersAdapterError(f"stage3 rulebook direction must be long: {direction!r}")
    return row_ticker


def _prepare_asset_meta(asset_meta: Mapping[str, Any], ticker: str) -> dict[str, Any]:
    if not isinstance(asset_meta, Mapping) or not asset_meta:
        raise ParametersAdapterError(f"asset_meta missing or invalid for {ticker}")
    asset = copy.deepcopy(dict(asset_meta))
    asset["ticker"] = _normalize_ticker(ticker)
    return asset


def _build_selected_member(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rulebook_hash": row.get("rulebook_hash"),
        "entry_rulebook_hash": row.get("entry_rulebook_hash"),
        "rank": row.get("rank"),
        "entry_rank": row.get("entry_rank"),
        "exit_rank": row.get("exit_rank"),
        "source_composite_fitness": row.get("source_composite_fitness", row.get("composite_fitness")),
        "holding_class": row.get("holding_class"),
        "risk_class": row.get("risk_class"),
        "return_class": row.get("return_class"),
        "composite_tag": row.get("composite_tag"),
        "pure_oos_periods": copy.deepcopy(row.get("pure_oos_validation_periods") or []),
        "pure_oos_metrics": copy.deepcopy(row.get("per_period_metrics") or {}),
        "stress_reference_metrics": copy.deepcopy(row.get("stress_reference_metrics") or row.get("stress_metrics") or {}),
        "bull_metrics": copy.deepcopy(row.get("bull_metrics") or {}),
        "stress_metrics": copy.deepcopy(row.get("stress_metrics") or {}),
        "holding_summary": copy.deepcopy(row.get("holding_summary") or row.get("all_oos_holding_summary") or {}),
    }


def _build_selection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": "stage3_profile_catalog",
        "period_results": copy.deepcopy(row.get("period_results") or {}),
        "per_period_metrics": copy.deepcopy(row.get("per_period_metrics") or {}),
        "profile_period_metrics": copy.deepcopy(row.get("profile_period_metrics") or {}),
        "all_oos_holding_summary": copy.deepcopy(row.get("all_oos_holding_summary") or {}),
        "profile_config": copy.deepcopy(row.get("profile_config") or {}),
        "eligibility_fail_reasons": copy.deepcopy(row.get("eligibility_fail_reasons") or []),
        "eligible_stage3_basic": row.get("eligible_stage3_basic"),
        "exit_check_period": copy.deepcopy(row.get("exit_check_period") or {}),
    }


def _dry_validate_live_payload(ticker: str, payload: Mapping[str, Any]) -> None:
    try:
        _validate_parameters(ticker, Path(f"<stage3-adapter>/{ticker}/parameters.json"), payload)
    except LiveUniverseError as exc:
        raise ParametersAdapterError(f"live universe validation failed for {ticker}: {exc}") from exc


def _payload_ticker(payload: Mapping[str, Any]) -> str:
    asset_meta = payload.get("asset_meta") if isinstance(payload, Mapping) else None
    rulebook = payload.get("rulebook") if isinstance(payload, Mapping) else None
    ticker = ""
    if isinstance(asset_meta, Mapping):
        ticker = _normalize_ticker(asset_meta.get("ticker"))
    if not ticker and isinstance(rulebook, Mapping):
        ticker = _normalize_ticker(rulebook.get("ticker"))
    if not ticker:
        raise ParametersAdapterError("parameters payload ticker missing")
    return ticker


def _json_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.bak.{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak.{stamp}.{counter}")
        counter += 1
    return candidate


def _tmp_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return path.with_name(f".{path.name}.tmp.{stamp}.{os.getpid()}")


def _is_under_live_symbols(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
        live_root = Path("data/symbols").resolve(strict=False)
        return resolved == live_root or resolved.is_relative_to(live_root)
    except Exception:
        return False


def _require_non_empty(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ParametersAdapterError(f"{name} must be non-empty")
    return text


def _normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _utc_iso(value: str | datetime | None) -> str:
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ParametersAdapterError("created_at must be non-empty when provided")
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
