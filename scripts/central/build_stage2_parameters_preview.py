#!/usr/bin/env python3
"""Build preview parameters.json files from Stage2 survivor entities.

This script intentionally writes only to a preview directory. It never writes to
``data/symbols``. It validates every source entity, then materializes one
parameters.json per ticker because the live-universe schema is ticker-scoped.

Duplicate ticker policy: first central_index occurrence wins. All duplicate
entities are retained in the summary report for later central-runner design.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.core.metadata import compute_member_hash, compute_rulebook_hash
from engine.live.universe import LiveUniverseConfig, load_live_universe, _validate_parameters

DEFAULT_BATCH_ROOT = Path("exp_batch_stage123_2009_20260616_full")
DEFAULT_OUT_DIR = Path("data/_system/central/stage2_b/symbols_preview")
DEFAULT_TICKER_UNIVERSE = Path("data/_system/ticker_universe.json")
DEFAULT_SYMBOLS_DIR = Path("data/symbols")
DEFAULT_OHLCV_DIR = Path("data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache")
DEFAULT_PROMOTION_ID = "central_stage2_survivor_preview_20260622"
DEFAULT_VERSION = "stage2-survivor-parameters-preview-v1"
DEFAULT_CREATED_AT = "2026-06-22T00:00:00Z"
DEFAULT_LIMIT = 533

US_TRADING_HOURS = {
    "timezone": "America/New_York",
    "open": "09:30",
    "close": "16:00",
    "pre_auction_end": None,
    "post_auction_start": None,
}


@dataclass(frozen=True)
class AssetMetaResolution:
    ticker: str
    source: str
    asset_meta: dict[str, Any]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityPayload:
    entity_id: str
    ticker: str
    rulebook_hash: str
    member_hash: str
    central_index_ordinal: int
    source_file: str
    source_row_index: int
    asset_meta_source: str
    asset_type_mismatch: bool
    etf_rulebook_stock_mismatch: bool
    sell_omen_enabled: bool
    payload: dict[str, Any]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def clean_output_dir(path: Path) -> None:
    if not path.exists():
        return
    resolved = path.resolve(strict=False)
    allowed = (PROJECT_ROOT / DEFAULT_OUT_DIR).resolve(strict=False)
    if resolved != allowed:
        raise RuntimeError(f"refusing to clean unexpected output dir: {path}")
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def load_first_stage2_survivor_index_rows(batch_root: Path, limit: int) -> list[dict[str, Any]]:
    index_path = batch_root / "central_index.jsonl"
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event_type") != "stage2_survivor":
            continue
        rows.append(row)
        if len(rows) >= int(limit):
            break
    return rows


def find_survivor_row(batch_root: Path, index_row: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    source_file = str(index_row.get("source_file") or "")
    rulebook_hash = str(index_row.get("rulebook_hash") or "")
    path = batch_root / source_file
    if not path.exists():
        raise FileNotFoundError(f"source_file missing: {path}")
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        if str(row.get("rulebook_hash") or "") == rulebook_hash:
            return row, i
    raise LookupError(f"rulebook_hash not found in {path}: {rulebook_hash}")


def load_ticker_universe(path: Path) -> dict[str, dict[str, Any]]:
    rows = read_json(path)
    if not isinstance(rows, list):
        raise ValueError(f"ticker universe must be list: {path}")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ticker = str(row.get("symbol") or "").strip().upper()
        if ticker:
            out[ticker] = dict(row)
    return out


def existing_asset_meta(ticker: str, symbols_dir: Path) -> dict[str, Any] | None:
    path = symbols_dir / ticker / "parameters.json"
    if not path.exists():
        return None
    payload = read_json(path)
    asset_meta = payload.get("asset_meta") if isinstance(payload, Mapping) else None
    if not isinstance(asset_meta, Mapping) or not asset_meta:
        return None
    out = copy.deepcopy(dict(asset_meta))
    out["ticker"] = ticker
    return out


def resolve_asset_meta(
    ticker: str,
    *,
    symbols_dir: Path,
    ticker_universe: Mapping[str, Mapping[str, Any]],
    ohlcv_dir: Path,
) -> AssetMetaResolution:
    ticker_u = str(ticker or "").strip().upper()
    existing = existing_asset_meta(ticker_u, symbols_dir)
    if existing is not None:
        return AssetMetaResolution(ticker=ticker_u, source="existing_data_symbols", asset_meta=existing)

    universe_row = ticker_universe.get(ticker_u)
    if not isinstance(universe_row, Mapping):
        raise KeyError(f"ticker_universe missing: {ticker_u}")
    cache_path = ohlcv_dir / f"{ticker_u}.pkl"
    if not cache_path.exists():
        raise FileNotFoundError(f"OHLCV cache missing for asset_meta fallback: {cache_path}")

    raw_type = str(universe_row.get("type") or "").strip()
    asset_type = "us_etf" if raw_type.lower() == "etf" else "us_stock"
    requires_fundamental = asset_type == "us_stock"
    exchange = str(universe_row.get("exchange") or "").strip()
    notes = (
        "market normalized to NYSE/NASDAQ for US-region live validator",
        "raw exchange preserved under extra.exchange",
        "OHLCV cache existence verified",
    )
    asset = {
        "ticker": ticker_u,
        "name": universe_row.get("name") or ticker_u,
        "asset_type": asset_type,
        "direction": "long",
        "currency": "USD",
        "market": "NYSE/NASDAQ",
        "trading_hours": copy.deepcopy(US_TRADING_HOURS),
        "requires_disclosure": requires_fundamental,
        "requires_earnings": requires_fundamental,
        "extra": {
            "source": str(DEFAULT_TICKER_UNIVERSE),
            "exchange": exchange,
            "type": raw_type,
            "ipo": universe_row.get("ipo"),
            "ohlcv_cache_verified": str(cache_path),
        },
    }
    return AssetMetaResolution(ticker=ticker_u, source="ticker_universe_plus_ohlcv_cache", asset_meta=asset, notes=notes)


def compact_period(period: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(period, Mapping):
        return {}
    keys = [
        "period_label",
        "period_kind",
        "expectancy_pct",
        "profit_factor",
        "win_rate",
        "trade_count",
        "max_drawdown_pct",
        "fitness",
        "member_score",
        "exit_reason_distribution",
    ]
    return {k: copy.deepcopy(period.get(k)) for k in keys if k in period}


def period_by_label(periods: list[Mapping[str, Any]], label: str) -> dict[str, Any]:
    return compact_period(next((p for p in periods if p.get("period_label") == label), {}))


def build_parameters_from_stage2_row(
    *,
    index_row: Mapping[str, Any],
    survivor_row: Mapping[str, Any],
    source_row_index: int,
    central_index_ordinal: int,
    asset_resolution: AssetMetaResolution,
    batch_root: Path,
    promotion_id: str,
    version: str,
    created_at: str,
) -> EntityPayload:
    ticker = str(index_row.get("ticker") or survivor_row.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("ticker missing")
    rulebook = survivor_row.get("rulebook")
    if not isinstance(rulebook, Mapping) or not rulebook:
        raise ValueError(f"rulebook missing: {ticker}")
    rb = copy.deepcopy(dict(rulebook))

    row_hash = str(index_row.get("rulebook_hash") or survivor_row.get("rulebook_hash") or "").strip()
    computed_rulebook_hash = compute_rulebook_hash(rb)
    computed_member_hash = compute_member_hash(rb)
    if row_hash and computed_rulebook_hash != row_hash:
        raise ValueError(f"rulebook_hash mismatch {ticker}: row={row_hash} computed={computed_rulebook_hash}")
    if row_hash and computed_member_hash != row_hash:
        raise ValueError(f"member_hash mismatch {ticker}: row={row_hash} computed={computed_member_hash}")
    rulebook_hash = row_hash or computed_rulebook_hash
    member_hash = computed_member_hash
    entity_id = f"{ticker}_{rulebook_hash[:12]}"

    periods = copy.deepcopy(list(survivor_row.get("periods") or []))
    origins = copy.deepcopy(list(survivor_row.get("origins") or []))
    metrics = {
        label: period_by_label(periods, label)
        for label in ["stress_pre_2022h1", "train_1_eval", "train_2_eval", "train_3_eval", "oos_2025h2"]
    }
    asset_meta = copy.deepcopy(asset_resolution.asset_meta)
    asset_meta["ticker"] = ticker

    asset_type = str(asset_meta.get("asset_type") or "").lower()
    rulebook_asset_type = str(rb.get("asset_type") or "").lower()
    asset_type_mismatch = bool(asset_type and rulebook_asset_type and asset_type != rulebook_asset_type)
    etf_rulebook_stock_mismatch = asset_type == "us_etf" and rulebook_asset_type == "us_stock"
    sell_omen_enabled = bool(rb.get("sell_omen_enabled", False))

    payload = {
        "asset_meta": asset_meta,
        "promotion": {
            "created_at": created_at,
            "promotion_id": promotion_id,
            "rulebook_hash": rulebook_hash,
            "member_hash": member_hash,
            "selected_member": {
                "source_stage": "stage2",
                "ticker": ticker,
                "entity_id": entity_id,
                "rulebook_hash": rulebook_hash,
                "member_hash": member_hash,
                "central_index_ordinal": central_index_ordinal,
                "source_file": str(index_row.get("source_file") or ""),
                "source_row_index": int(source_row_index),
                "origin_train_labels": copy.deepcopy(index_row.get("origin_train_labels") or []),
                "origin_count": index_row.get("origin_count"),
                "oos_2025h2": metrics.get("oos_2025h2", {}),
                "stress_pre_2022h1": metrics.get("stress_pre_2022h1", {}),
            },
            "selection": {
                "source": "stage2_survivor",
                "run_id": "stage123_2009_20260616_full",
                "run_root": str(batch_root),
                "central_index": str(batch_root / "central_index.jsonl"),
                "source_file": str(index_row.get("source_file") or ""),
                "source_row_index": int(source_row_index),
                "metrics": metrics,
                "origins": origins,
                "origin_train_labels": copy.deepcopy(index_row.get("origin_train_labels") or []),
                "origin_count": index_row.get("origin_count"),
                "asset_meta_source": asset_resolution.source,
                "asset_meta_notes": list(asset_resolution.notes),
                "rulebook_settings_policy": "preserve_original_stage2_rulebook_fields_including_sell_omen",
                "survived_all_5": True,
            },
            "selection_filter": {
                "source": "stage2_survivor_preview",
                "version": version,
                "input": f"first {DEFAULT_LIMIT} stage2_survivor rows from central_index",
                "data_symbols_write": False,
                "duplicate_policy": "first central_index occurrence per ticker is materialized to <TICKER>/parameters.json; all entities are validated and reported",
            },
            "source": "central_stage2_survivor",
            "source_run_dir": str(batch_root),
            "source_run_id": "stage123_2009_20260616_full",
        },
        "rulebook": rb,
        "saved_at": created_at,
        "version": version,
    }
    _validate_parameters(ticker, Path(f"<preview>/{ticker}/parameters.json"), payload)
    return EntityPayload(
        entity_id=entity_id,
        ticker=ticker,
        rulebook_hash=rulebook_hash,
        member_hash=member_hash,
        central_index_ordinal=central_index_ordinal,
        source_file=str(index_row.get("source_file") or ""),
        source_row_index=int(source_row_index),
        asset_meta_source=asset_resolution.source,
        asset_type_mismatch=asset_type_mismatch,
        etf_rulebook_stock_mismatch=etf_rulebook_stock_mismatch,
        sell_omen_enabled=sell_omen_enabled,
        payload=payload,
    )


def materialize_preview(entity_payloads: list[EntityPayload], out_dir: Path, promotion_id: str) -> dict[str, Any]:
    selected_by_ticker: dict[str, EntityPayload] = {}
    duplicate_entities_by_ticker: dict[str, list[EntityPayload]] = defaultdict(list)
    for entity in entity_payloads:
        if entity.ticker not in selected_by_ticker:
            selected_by_ticker[entity.ticker] = entity
        else:
            duplicate_entities_by_ticker[entity.ticker].append(entity)

    for ticker, entity in sorted(selected_by_ticker.items()):
        write_json(out_dir / ticker / "parameters.json", entity.payload)

    duplicate_report = []
    for ticker, duplicates in sorted(duplicate_entities_by_ticker.items()):
        selected = selected_by_ticker[ticker]
        duplicate_report.append(
            {
                "ticker": ticker,
                "selected_entity_id": selected.entity_id,
                "selected_rulebook_hash": selected.rulebook_hash,
                "selected_central_index_ordinal": selected.central_index_ordinal,
                "duplicate_count_excluding_selected": len(duplicates),
                "duplicate_entities": [
                    {
                        "entity_id": ent.entity_id,
                        "rulebook_hash": ent.rulebook_hash,
                        "central_index_ordinal": ent.central_index_ordinal,
                        "source_file": ent.source_file,
                        "source_row_index": ent.source_row_index,
                        "sell_omen_enabled": ent.sell_omen_enabled,
                    }
                    for ent in duplicates
                ],
            }
        )
    write_jsonl(out_dir / "_all_entities_preview.jsonl", [
        {
            "entity_id": ent.entity_id,
            "ticker": ent.ticker,
            "rulebook_hash": ent.rulebook_hash,
            "central_index_ordinal": ent.central_index_ordinal,
            "source_file": ent.source_file,
            "source_row_index": ent.source_row_index,
            "asset_meta_source": ent.asset_meta_source,
            "asset_type_mismatch": ent.asset_type_mismatch,
            "etf_rulebook_stock_mismatch": ent.etf_rulebook_stock_mismatch,
            "sell_omen_enabled": ent.sell_omen_enabled,
            "materialized_to_ticker_parameters": selected_by_ticker[ent.ticker].entity_id == ent.entity_id,
        }
        for ent in entity_payloads
    ])
    write_json(out_dir / "_duplicate_ticker_report.json", {"duplicate_policy": "first_seen", "rows": duplicate_report})

    # Isolation check: with preview promotion id, all materialized tickers are eligible.
    preview_universe = load_live_universe(
        LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id=promotion_id, symbols_dir=out_dir)
    )
    # With current paper promotion id, no preview ticker should be eligible.
    current_paper_universe = load_live_universe(
        LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id="lr8d_stage1_20260609", symbols_dir=out_dir)
    )
    return {
        "materialized_tickers": len(selected_by_ticker),
        "duplicate_tickers": len(duplicate_entities_by_ticker),
        "duplicate_entities_excluding_selected": sum(len(v) for v in duplicate_entities_by_ticker.values()),
        "preview_universe_summary": preview_universe.summary(),
        "preview_universe_symbols": list(preview_universe.symbols),
        "current_paper_promotion_summary": current_paper_universe.summary(),
        "current_paper_promotion_symbols": list(current_paper_universe.symbols),
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    batch_root = Path(args.batch_root)
    out_dir = Path(args.out_dir)
    if args.clean_output:
        clean_output_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ticker_universe = load_ticker_universe(Path(args.ticker_universe))
    index_rows = load_first_stage2_survivor_index_rows(batch_root, int(args.limit))
    if len(index_rows) != int(args.limit):
        raise RuntimeError(f"expected {args.limit} stage2 survivor rows, got {len(index_rows)}")

    entity_payloads: list[EntityPayload] = []
    failures: list[dict[str, Any]] = []
    for ordinal, index_row in enumerate(index_rows, start=1):
        ticker = str(index_row.get("ticker") or "").strip().upper()
        try:
            survivor_row, source_row_index = find_survivor_row(batch_root, index_row)
            asset_resolution = resolve_asset_meta(
                ticker,
                symbols_dir=Path(args.symbols_dir),
                ticker_universe=ticker_universe,
                ohlcv_dir=Path(args.ohlcv_dir),
            )
            entity_payloads.append(
                build_parameters_from_stage2_row(
                    index_row=index_row,
                    survivor_row=survivor_row,
                    source_row_index=source_row_index,
                    central_index_ordinal=ordinal,
                    asset_resolution=asset_resolution,
                    batch_root=batch_root,
                    promotion_id=str(args.promotion_id),
                    version=str(args.version),
                    created_at=str(args.created_at),
                )
            )
        except Exception as exc:
            failures.append(
                {
                    "central_index_ordinal": ordinal,
                    "ticker": ticker,
                    "rulebook_hash": index_row.get("rulebook_hash"),
                    "source_file": index_row.get("source_file"),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    if failures:
        write_json(out_dir / "_failures.json", {"failures": failures})
        raise RuntimeError(f"conversion failures: {len(failures)}; see {out_dir / '_failures.json'}")

    materialize = materialize_preview(entity_payloads, out_dir, str(args.promotion_id))
    counts = Counter()
    unique_by_source: dict[str, set[str]] = defaultdict(set)
    mismatches = []
    sell_omen_true = []
    for ent in entity_payloads:
        counts["entities"] += 1
        counts[f"asset_meta_source:{ent.asset_meta_source}"] += 1
        unique_by_source[ent.asset_meta_source].add(ent.ticker)
        if ent.asset_type_mismatch:
            counts["asset_type_mismatch"] += 1
            mismatches.append(
                {
                    "entity_id": ent.entity_id,
                    "ticker": ent.ticker,
                    "asset_meta_asset_type": ent.payload["asset_meta"].get("asset_type"),
                    "rulebook_asset_type": ent.payload["rulebook"].get("asset_type"),
                    "etf_rulebook_stock_mismatch": ent.etf_rulebook_stock_mismatch,
                }
            )
        if ent.etf_rulebook_stock_mismatch:
            counts["etf_rulebook_stock_mismatch"] += 1
        if ent.sell_omen_enabled:
            counts["sell_omen_enabled_true"] += 1
            sell_omen_true.append({"entity_id": ent.entity_id, "ticker": ent.ticker, "rulebook_hash": ent.rulebook_hash})

    unique_tickers = sorted({ent.ticker for ent in entity_payloads})
    summary = {
        "purpose": "Stage2 survivor to parameters.json preview only; no data/symbols writes",
        "input": {
            "batch_root": str(batch_root),
            "central_index": str(batch_root / "central_index.jsonl"),
            "stage2_survivor_limit": int(args.limit),
        },
        "output": {
            "out_dir": str(out_dir),
            "ticker_parameters_pattern": "<out_dir>/<TICKER>/parameters.json",
            "all_entities_report": str(out_dir / "_all_entities_preview.jsonl"),
            "duplicate_report": str(out_dir / "_duplicate_ticker_report.json"),
        },
        "policy": {
            "promotion_id": str(args.promotion_id),
            "version": str(args.version),
            "duplicate_ticker_policy": "first_seen central_index occurrence wins for <TICKER>/parameters.json",
            "rulebook_policy": "preserve original Stage2 rulebook fields, including sell_omen settings",
            "asset_meta_policy": [
                "reuse existing data/symbols/<ticker>/parameters.json asset_meta when present",
                "otherwise use data/_system/ticker_universe.json plus OHLCV cache existence verification",
                "normalize US market to NYSE/NASDAQ and preserve raw exchange/type/ipo under extra",
            ],
            "data_symbols_write": False,
        },
        "counts": {
            **dict(counts),
            "unique_tickers": len(unique_tickers),
            "validation_pass_entities": len(entity_payloads),
            "validation_fail_entities": len(failures),
            "materialized_ticker_parameter_files": materialize["materialized_tickers"],
            "duplicate_tickers": materialize["duplicate_tickers"],
            "duplicate_entities_excluding_selected": materialize["duplicate_entities_excluding_selected"],
        },
        "asset_meta_unique_tickers_by_source": {k: len(v) for k, v in sorted(unique_by_source.items())},
        "asset_type_mismatches": mismatches,
        "sell_omen_enabled_true_entities": sell_omen_true,
        "promotion_isolation": {
            "preview_promotion_summary": materialize["preview_universe_summary"],
            "current_paper_promotion_summary": materialize["current_paper_promotion_summary"],
            "current_paper_promotion_eligible_symbols_should_be_zero": materialize["current_paper_promotion_symbols"],
        },
    }
    write_json(out_dir / "_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Stage2 survivor parameters.json preview files")
    p.add_argument("--batch-root", default=str(DEFAULT_BATCH_ROOT))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--symbols-dir", default=str(DEFAULT_SYMBOLS_DIR))
    p.add_argument("--ticker-universe", default=str(DEFAULT_TICKER_UNIVERSE))
    p.add_argument("--ohlcv-dir", default=str(DEFAULT_OHLCV_DIR))
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument("--promotion-id", default=DEFAULT_PROMOTION_ID)
    p.add_argument("--version", default=DEFAULT_VERSION)
    p.add_argument("--created-at", default=DEFAULT_CREATED_AT)
    p.add_argument("--clean-output", action="store_true", help="Remove existing preview output before writing")
    return p.parse_args()


if __name__ == "__main__":
    build(parse_args())
