"""CLI for building Stage3-derived parameters payloads.

Default mode is dry-run. Actual writes require ``--write`` and write to a
separate output directory unless live-symbol writing is explicitly allowed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

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

DEFAULT_OUT_DIR = Path("data/_system/central/parameters_out")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        catalog_rows = load_stage3_catalog(args.catalog)
        final_rows = load_final_rulebooks(args.final)
        join_stats = join_stats_by_rulebook_hash(catalog_rows, final_rows)
        rows = join_stage3_rows_by_rulebook_hash(catalog_rows, final_rows)
        row = _select_row(rows, ticker=args.ticker, rulebook_hash=args.rulebook_hash, rank=args.rank)
        ticker = str(row.get("ticker") or "").strip().upper()
        asset_meta = load_asset_meta_for_ticker(ticker, symbols_dir=args.symbols_dir)
        payload = build_parameters_from_stage3_row(
            row,
            asset_meta=asset_meta,
            promotion_id=args.promotion_id,
            source_run_dir=args.source_run_dir,
            source_run_id=args.source_run_id,
            version=args.version,
        )
        out_path = Path(args.out_dir) / ticker / "parameters.json"
        report = write_parameters(
            payload,
            out_path,
            dry_run=not args.write,
            backup=True,
            allow_live_symbols=bool(args.allow_live_symbols),
        )
        output = {
            "ok": True,
            "mode": "write" if args.write else "dry_run",
            "selected": {
                "ticker": ticker,
                "rulebook_hash": row.get("rulebook_hash"),
                "rank": row.get("rank"),
                "entry_rank": row.get("entry_rank"),
                "exit_rank": row.get("exit_rank"),
            },
            "join_stats": {
                "catalog_rows": join_stats.catalog_rows,
                "final_rows": join_stats.final_rows,
                "joined_rows": join_stats.joined_rows,
                "catalog_only": join_stats.catalog_only,
                "final_only": join_stats.final_only,
            },
            "write_report": report,
            "payload_summary": {
                "top_keys": sorted(payload.keys()),
                "asset_meta_ticker": payload["asset_meta"].get("ticker"),
                "promotion_keys": sorted(payload["promotion"].keys()),
                "promotion_id": payload["promotion"].get("promotion_id"),
                "rulebook_hash": payload["promotion"].get("rulebook_hash"),
                "member_hash": payload["promotion"].get("member_hash"),
                "rulebook_ticker": payload["rulebook"].get("ticker"),
                "rulebook_direction": payload["rulebook"].get("direction"),
                "rulebook_key_count": len(payload["rulebook"]),
                "version": payload.get("version"),
            },
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except Exception as exc:
        error = {
            "ok": False,
            "error_class": exc.__class__.__name__,
            "error": str(exc),
        }
        print(json.dumps(error, ensure_ascii=False, sort_keys=True, indent=2), file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Stage3-derived parameters payloads without touching live symbols by default.")
    parser.add_argument("--catalog", required=True, help="Path to stage3_profile_catalog.jsonl")
    parser.add_argument("--final", required=True, help="Path to final_rulebooks.jsonl")
    parser.add_argument("--ticker", required=True, help="Ticker to export")
    parser.add_argument("--rulebook-hash", default="", help="Exact rulebook_hash to select")
    parser.add_argument("--rank", type=int, default=1, help="Fallback rank to select when --rulebook-hash is omitted")
    parser.add_argument("--promotion-id", required=True, help="Non-empty promotion id to embed")
    parser.add_argument("--version", required=True, help="Non-empty adapter/version string")
    parser.add_argument("--source-run-dir", required=True, help="Source Stage3 run directory")
    parser.add_argument("--source-run-id", required=True, help="Source Stage3 run id")
    parser.add_argument("--symbols-dir", default="data/symbols", help="Read-only source for asset_meta")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output root; defaults outside live data/symbols")
    parser.add_argument("--write", action="store_true", help="Actually write; default is dry-run")
    parser.add_argument("--allow-live-symbols", action="store_true", help="Allow writing under data/symbols; backup is forced")
    return parser


def _select_row(rows: list[dict[str, Any]], *, ticker: str, rulebook_hash: str = "", rank: int = 1) -> dict[str, Any]:
    ticker_u = str(ticker or "").strip().upper()
    if not ticker_u:
        raise ParametersAdapterError("ticker must be non-empty")
    ticker_rows = [row for row in rows if str(row.get("ticker") or "").strip().upper() == ticker_u]
    if not ticker_rows:
        raise ParametersAdapterError(f"no joined Stage3 rows for ticker {ticker_u}")
    hash_s = str(rulebook_hash or "").strip()
    if hash_s:
        matches = [row for row in ticker_rows if str(row.get("rulebook_hash") or "").strip() == hash_s]
        if len(matches) != 1:
            raise ParametersAdapterError(f"expected exactly one row for {ticker_u} rulebook_hash={hash_s}, got {len(matches)}")
        return matches[0]
    rank_i = int(rank or 1)
    rank_matches = [row for row in ticker_rows if _safe_int(row.get("rank")) == rank_i]
    if len(rank_matches) == 1:
        return rank_matches[0]
    if len(rank_matches) > 1:
        raise ParametersAdapterError(f"multiple rows for {ticker_u} rank={rank_i}")
    sorted_rows = sorted(ticker_rows, key=lambda row: (_safe_int(row.get("rank"), default=10**9), str(row.get("rulebook_hash") or "")))
    if rank_i == 1 and sorted_rows:
        return sorted_rows[0]
    raise ParametersAdapterError(f"no row for {ticker_u} rank={rank_i}")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


if __name__ == "__main__":
    raise SystemExit(main())
