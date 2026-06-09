#!/usr/bin/env python3
"""Export LR8D stage-1 paper universe parameters.

Selection policy:
- source run: data/_system/research/lr8d_abcd_20260608
- combo_id == strict_k3
- unique selected_rulebook_hash
- worst_drawdown_pct > -25
- stress_worst_expectancy_pct >= 0

The live loader does not consume a standalone universe JSON.  It scans
``data/symbols/<ticker>/parameters.json`` and filters by promotion_id.  This
script therefore builds validated parameters payloads for the selected symbols
and, when explicitly confirmed with --apply, writes those parameters with a new
promotion id so ``scripts/run_live.py --promotion-id lr8d_stage1_20260609``
loads only the stage-1 universe.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.strategies.rulebook import Rulebook

RUN_ID = "lr8d_abcd_20260608"
DEFAULT_RUN_DIR = Path(f"data/_system/research/{RUN_ID}")
DEFAULT_SYMBOLS_DIR = Path("data/symbols")
DEFAULT_PROMOTION_ID = "lr8d_stage1_20260609"
DEFAULT_MANIFEST_PATH = Path("data/_system/live_universe_lr8d_stage1_manifest.json")
DD_CUTOFF = -25.0


@dataclass(frozen=True)
class Stage1Selection:
    ticker: str
    selected_rulebook_hash: str
    survivor: dict[str, Any]
    rulebook: dict[str, Any]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except Exception as exc:  # pragma: no cover - defensive message
                raise ValueError(f"invalid JSONL: {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be object: {path}:{line_no}")
            rows.append(row)
    return rows


def load_rulebooks_by_hash(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "lr8d_abcd_topn_rulebooks.jsonl"
    rulebooks: dict[str, dict[str, Any]] = {}
    for row in _load_jsonl(path):
        h = str(row.get("rulebook_hash") or "").strip()
        rb = row.get("rulebook")
        if not h or not isinstance(rb, dict):
            continue
        rulebooks.setdefault(h, dict(rb))
    return rulebooks


def select_stage1_survivors(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "lr8d_abcd_survivors.jsonl"
    selected: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for row in _load_jsonl(path):
        if row.get("combo_id") != "strict_k3":
            continue
        h = str(row.get("selected_rulebook_hash") or row.get("rulebook_hash") or "").strip()
        if not h or h in seen_hashes:
            continue
        dd = float(row.get("worst_drawdown_pct", 0.0) or 0.0)
        stress = float(row.get("stress_worst_expectancy_pct", 0.0) or 0.0)
        if dd <= DD_CUTOFF:
            continue
        if stress < 0.0:
            continue
        seen_hashes.add(h)
        selected.append(row)
    selected.sort(key=lambda r: str(r.get("ticker") or ""))
    return selected


def build_stage1_selection(run_dir: Path) -> list[Stage1Selection]:
    rulebooks = load_rulebooks_by_hash(run_dir)
    selections: list[Stage1Selection] = []
    missing: list[str] = []
    for survivor in select_stage1_survivors(run_dir):
        ticker = str(survivor.get("ticker") or "").strip().upper()
        h = str(survivor.get("selected_rulebook_hash") or survivor.get("rulebook_hash") or "").strip()
        rb = rulebooks.get(h)
        if rb is None:
            missing.append(f"{ticker}:{h}")
            continue
        rb = dict(rb)
        rb["ticker"] = ticker
        rb.setdefault("asset_type", "us_stock")
        rb.setdefault("direction", "long")
        selections.append(Stage1Selection(ticker=ticker, selected_rulebook_hash=h, survivor=dict(survivor), rulebook=rb))
    if missing:
        raise RuntimeError(f"selected rulebook hash missing from topn_rulebooks: {missing}")
    return selections


def _read_existing_parameters(symbols_dir: Path, ticker: str) -> dict[str, Any]:
    path = symbols_dir / ticker / "parameters.json"
    if not path.exists():
        raise FileNotFoundError(f"existing parameters missing for {ticker}: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"parameters root must be object for {ticker}: {path}")
    return data


def _selection_meta(survivor: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "combo_id",
        "eligible_year_count",
        "eligible_years",
        "avg_expectancy_pct",
        "min_expectancy_pct",
        "avg_win_rate",
        "avg_profit_factor",
        "avg_trades",
        "selected_rulebook_expectancy_pct",
        "selected_rulebook_win_rate",
        "selected_rulebook_profit_factor",
        "selected_rulebook_trade_count",
        "selected_rulebook_source_label",
        "worst_drawdown_pct",
        "stress_worst_expectancy_pct",
        "stress_avg_expectancy_pct",
        "stress_appearance_count",
        "worst_year_member_score",
    ]
    return {k: survivor.get(k) for k in keys if k in survivor}


def build_parameters_payload(
    selection: Stage1Selection,
    *,
    symbols_dir: Path = DEFAULT_SYMBOLS_DIR,
    promotion_id: str = DEFAULT_PROMOTION_ID,
    exported_at: str | None = None,
) -> dict[str, Any]:
    existing = _read_existing_parameters(symbols_dir, selection.ticker)
    asset_meta = dict(existing.get("asset_meta") or {})
    if not asset_meta:
        raise ValueError(f"{selection.ticker}: asset_meta missing in existing parameters")
    asset_meta["ticker"] = selection.ticker
    asset_meta.setdefault("asset_type", "us_stock")
    asset_meta.setdefault("currency", "USD")
    asset_meta.setdefault("direction", "long")
    asset_meta.setdefault("market", "NYSE/NASDAQ")

    rb = dict(selection.rulebook)
    rb["ticker"] = selection.ticker
    rb.setdefault("asset_type", "us_stock")
    rb.setdefault("direction", "long")
    # Validate before writing.  The live universe performs this same parse.
    Rulebook.from_dict(dict(rb))

    exported_at = exported_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    survivor = selection.survivor
    payload = {
        "version": "lr8d_stage1_v1",
        "saved_at": exported_at,
        "asset_meta": asset_meta,
        "rulebook": rb,
        "promotion": {
            "promotion_id": promotion_id,
            "created_at": exported_at,
            "source_run_id": RUN_ID,
            "source_run_dir": str(DEFAULT_RUN_DIR),
            "source": "LR8D strict_k3 stage1 filter",
            "rulebook_hash": selection.selected_rulebook_hash,
            "member_hash": selection.selected_rulebook_hash,
            "selection_filter": {
                "combo_id": "strict_k3",
                "worst_drawdown_pct_gt": DD_CUTOFF,
                "stress_worst_expectancy_pct_gte": 0.0,
                "unique_by": "selected_rulebook_hash",
            },
            "selected_member": {
                "expectancy_pct": survivor.get("selected_rulebook_expectancy_pct"),
                "win_rate": survivor.get("selected_rulebook_win_rate"),
                "profit_factor": survivor.get("selected_rulebook_profit_factor"),
                "trade_count": survivor.get("selected_rulebook_trade_count"),
                "worst_drawdown_pct": survivor.get("worst_drawdown_pct"),
                "stress_worst_expectancy_pct": survivor.get("stress_worst_expectancy_pct"),
                "source_label": survivor.get("selected_rulebook_source_label"),
            },
            "selection": _selection_meta(survivor),
        },
    }
    return payload


def build_manifest(
    selections: Iterable[Stage1Selection],
    *,
    promotion_id: str,
    exported_at: str,
) -> dict[str, Any]:
    items = []
    for s in selections:
        survivor = s.survivor
        items.append(
            {
                "ticker": s.ticker,
                "selected_rulebook_hash": s.selected_rulebook_hash,
                "combo_id": survivor.get("combo_id"),
                "selected_rulebook_expectancy_pct": survivor.get("selected_rulebook_expectancy_pct"),
                "selected_rulebook_win_rate": survivor.get("selected_rulebook_win_rate"),
                "selected_rulebook_profit_factor": survivor.get("selected_rulebook_profit_factor"),
                "selected_rulebook_trade_count": survivor.get("selected_rulebook_trade_count"),
                "worst_drawdown_pct": survivor.get("worst_drawdown_pct"),
                "stress_worst_expectancy_pct": survivor.get("stress_worst_expectancy_pct"),
                "selected_rulebook_source_label": survivor.get("selected_rulebook_source_label"),
            }
        )
    items.sort(key=lambda row: row["ticker"])
    return {
        "promotion_id": promotion_id,
        "run_id": RUN_ID,
        "exported_at": exported_at,
        "filter": {
            "combo_id": "strict_k3",
            "worst_drawdown_pct_gt": DD_CUTOFF,
            "stress_worst_expectancy_pct_gte": 0.0,
            "unique_by": "selected_rulebook_hash",
        },
        "count": len(items),
        "tickers": [row["ticker"] for row in items],
        "items": items,
        "live_command_hint": f"venv/bin/python scripts/run_live.py --universe promoted --promotion-id {promotion_id}",
    }


def export_stage1(
    *,
    run_dir: Path = DEFAULT_RUN_DIR,
    symbols_dir: Path = DEFAULT_SYMBOLS_DIR,
    promotion_id: str = DEFAULT_PROMOTION_ID,
    apply: bool = False,
    confirm_promotion_id: str = "",
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    symbols_dir = Path(symbols_dir)
    selections = build_stage1_selection(run_dir)
    if len(selections) != 16:
        raise RuntimeError(f"expected 16 stage1 selections, got {len(selections)}")

    exported_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payloads: dict[str, dict[str, Any]] = {}
    for selection in selections:
        payloads[selection.ticker] = build_parameters_payload(
            selection,
            symbols_dir=symbols_dir,
            promotion_id=promotion_id,
            exported_at=exported_at,
        )

    manifest = build_manifest(selections, promotion_id=promotion_id, exported_at=exported_at)

    if apply:
        if confirm_promotion_id != promotion_id:
            raise RuntimeError(
                f"refusing to write parameters without exact --confirm-promotion-id {promotion_id!r}"
            )
        for ticker, payload in payloads.items():
            target_dir = symbols_dir / ticker
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "parameters.json"
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export LR8D strict_k3 stage1 paper universe")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--symbols-dir", type=Path, default=DEFAULT_SYMBOLS_DIR)
    parser.add_argument("--promotion-id", default=DEFAULT_PROMOTION_ID)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-promotion-id", default="")
    args = parser.parse_args()

    manifest = export_stage1(
        run_dir=args.run_dir,
        symbols_dir=args.symbols_dir,
        promotion_id=args.promotion_id,
        apply=args.apply,
        confirm_promotion_id=args.confirm_promotion_id,
        manifest_path=args.manifest_path,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    if not args.apply:
        print("\n[dry-run] no files written; add --apply --confirm-promotion-id", args.promotion_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
