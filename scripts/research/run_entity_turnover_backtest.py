#!/usr/bin/env python3
"""Provisional entity-mode turnover/confidence backtest runner."""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Iterable

from engine.central.allocation_policy import AllocationParams
from engine.central.backtester import BacktestResult, build_signal_cache, run_central_backtest
from engine.central.models import normalize_ticker
from engine.central.policy_search import apply_confidence_metric
from engine.central.signal_collector import SignalSnapshot
from engine.central.stage2_survivor_loader import load_stage2_survivors_with_report
from engine.live.central_control import _adjusted_confidence_from_metrics
from engine.live.universe import LiveUniverseConfig, load_live_universe

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "data" / "_system" / "central" / "stage2_b" / "entity_turnover_backtest"
DEFAULT_BATCH_ROOT = PROJECT_ROOT / "exp_batch_stage123_2009_20260616_full"
DEFAULT_CENTRAL_INDEX = DEFAULT_BATCH_ROOT / "central_index.jsonl"
DEFAULT_CONFIDENCE_PATH = PROJECT_ROOT / "data" / "_system" / "central" / "stage2_b" / "swap_score_test2" / "entity_confidence_oos.json"
PROMOTION_ID = "lr8d_stage1_20260609"
INITIAL_CAPITAL = 100_000.0
PERIODS = {
    "stress": ("2022-01-01", "2022-06-30"),
    "mid": ("2024-07-01", "2025-06-30"),
    "oos": ("2025-07-01", "2026-06-15"),
}
MARKER = "PROVISIONAL_PRE_BATCH"


def _safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def load_pool_entities() -> list:
    universe = load_live_universe(
        LiveUniverseConfig(market="US", universe_mode="promoted", promotion_id=PROMOTION_ID)
    )
    symbols = {normalize_ticker(ticker) for ticker in universe.symbols}
    confidence_payload = json.loads(DEFAULT_CONFIDENCE_PATH.read_text(encoding="utf-8"))
    allowed_entity_ids = set(list(confidence_payload.keys())[:533])
    report = load_stage2_survivors_with_report(
        str(DEFAULT_CENTRAL_INDEX),
        str(DEFAULT_BATCH_ROOT),
        tickers=symbols,
    )
    entities = apply_confidence_metric(report.entities, "profit_factor")
    out = []
    for entity in entities:
        if entity.entity_id not in allowed_entity_ids:
            continue
        if normalize_ticker(entity.ticker) not in symbols:
            continue
        adjusted_confidence = _adjusted_confidence_from_metrics(
            getattr(entity, "validation_metrics", {}) or {},
            pf_cap=10,
            min_trades=15,
        )
        out.append(replace(entity, confidence=adjusted_confidence))
    return out


def build_turnover_tag_cache(entities: Iterable, out_dir: Path) -> tuple[dict, dict]:
    entities = list(entities)
    needed_hashes = {str(entity.rulebook_hash) for entity in entities}
    tickers = {normalize_ticker(entity.ticker) for entity in entities}
    stats = {
        rulebook_hash: {"trade_count": 0, "pnl_sum": 0.0, "holding_sum": 0.0}
        for rulebook_hash in needed_hashes
    }
    files_scanned = 0
    rows_scanned = 0
    matched_trades = 0
    trades_root = DEFAULT_BATCH_ROOT / "tickers"
    for ticker in sorted(tickers):
        trades_path = trades_root / ticker / "stage2" / "trades.jsonl"
        if not trades_path.exists():
            continue
        files_scanned += 1
        with trades_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                rows_scanned += 1
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                rulebook_hash = str(row.get("member_hash") or row.get("rulebook_hash") or "")
                if rulebook_hash not in needed_hashes:
                    continue
                pnl_pct = _safe_float(row.get("pnl_pct"))
                holding_days = _safe_float(row.get("holding_days"))
                if pnl_pct is None or holding_days is None or holding_days <= 0.0:
                    continue
                item = stats[rulebook_hash]
                item["trade_count"] += 1
                item["pnl_sum"] += pnl_pct
                item["holding_sum"] += holding_days
                matched_trades += 1
    cache = {}
    missing = []
    for entity in entities:
        item = stats.get(str(entity.rulebook_hash)) or {}
        trade_count = int(item.get("trade_count") or 0)
        if trade_count <= 0:
            missing.append(entity.entity_id)
            continue
        cache[entity.entity_id] = {
            "entity_id": entity.entity_id,
            "ticker": normalize_ticker(entity.ticker),
            "rulebook_hash": str(entity.rulebook_hash),
            "avg_realized_pnl_pct": float(item["pnl_sum"]) / trade_count,
            "avg_holding_days": float(item["holding_sum"]) / trade_count,
            "trade_count": trade_count,
        }
    meta = {
        "marker": MARKER,
        "raw_entities": len(entities),
        "tagged_entities": len(cache),
        "missing_turnover_entities": len(missing),
        "missing_turnover_entity_ids": missing,
        "files_scanned": files_scanned,
        "rows_scanned": rows_scanned,
        "matched_trades": matched_trades,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "turnover_tag_cache.json").write_text(
        json.dumps({"meta": meta, "entities": cache}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cache, meta


def apply_turnover_tags(entities: Iterable, tag_cache: dict) -> tuple[list, list[str]]:
    out = []
    missing = []
    for entity in entities:
        tag = tag_cache.get(entity.entity_id)
        if tag is None:
            missing.append(entity.entity_id)
            continue
        tags = dict(entity.tags or {})
        tags.update(
            {
                "avg_realized_pnl_pct": tag["avg_realized_pnl_pct"],
                "avg_holding_days": tag["avg_holding_days"],
                "trade_count": tag["trade_count"],
            }
        )
        out.append(replace(entity, tags=tags))
    return out, missing


def serialize_signal_cache(signal_cache: dict[str, list[SignalSnapshot]], path: Path) -> None:
    rows = {date: [asdict(snapshot) for snapshot in snapshots] for date, snapshots in sorted(signal_cache.items())}
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def run_variant(
    *,
    period_name: str,
    start: str,
    end: str,
    variant: str,
    entities: list,
    signal_cache: dict[str, list[SignalSnapshot]],
    selection_metric: str,
    min_trades_for_turnover: int,
    out_dir: Path,
) -> dict:
    allocation_stats = {}
    params = AllocationParams(
        max_positions=8,
        total_capital=INITIAL_CAPITAL,
        position_sizing="score_weighted",
        cash_buffer_ratio=0.98,
        per_ticker_exposure_cap=0.25,
        confidence_weight=0.5,
        signal_strength_weight=0.5,
        min_confidence=0.0,
        allow_same_ticker_entities=True,
        allocation_stats=allocation_stats,
    )
    ledger_dir = out_dir / "ledgers" / f"{period_name}_{variant}"
    if ledger_dir.exists():
        shutil.rmtree(ledger_dir)
    t0 = time.perf_counter()
    result = run_central_backtest(
        entities,
        start,
        end,
        params,
        ledger_dir=ledger_dir,
        persist_ledger=True,
        flush_ledger_on_finish=True,
        selection_metric=selection_metric,
        min_trades_for_turnover=min_trades_for_turnover,
        signal_cache=signal_cache,
    )
    elapsed = time.perf_counter() - t0
    row = result_to_row(
        result,
        period_name=period_name,
        start=start,
        end=end,
        variant=variant,
        selection_metric=selection_metric,
        min_trades_for_turnover=min_trades_for_turnover,
        elapsed_seconds=elapsed,
        ledger_dir=str(ledger_dir),
    )
    (out_dir / f"result_{period_name}_{variant}.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return row


def result_to_row(
    result: BacktestResult,
    *,
    period_name: str,
    start: str,
    end: str,
    variant: str,
    selection_metric: str,
    min_trades_for_turnover: int,
    elapsed_seconds: float,
    ledger_dir: str,
) -> dict:
    diagnostics = result.diagnostics or {}
    selection_stats = diagnostics.get("selection_stats", {}) or {}
    return {
        "marker": MARKER,
        "period": period_name,
        "start": start,
        "end": end,
        "variant": variant,
        "selection_metric": selection_metric,
        "min_trades_for_turnover": min_trades_for_turnover if selection_metric == "turnover_score" else "",
        "return_pct": result.total_return,
        "mdd_pct": result.max_drawdown_pct,
        "return_mdd": result.total_return / abs(result.max_drawdown_pct) if result.max_drawdown_pct else "",
        "trade_count": len(result.trades),
        "buy_count": sum(1 for trade in result.trades if trade.side == "buy"),
        "sell_count": sum(1 for trade in result.trades if trade.side == "sell"),
        "final_equity": result.final_equity,
        "elapsed_seconds": elapsed_seconds,
        "avg_open_entity_positions": diagnostics.get("avg_open_entity_positions"),
        "max_open_entity_positions": diagnostics.get("max_open_entity_positions"),
        "ticker_cap_hit_events": diagnostics.get("ticker_cap_hit_events"),
        "ticker_cap_hit_tickers": diagnostics.get("ticker_cap_hit_tickers"),
        "turnover_excluded_min_trades": selection_stats.get("turnover_excluded_min_trades", ""),
        "turnover_scored_entities": selection_stats.get("turnover_scored_entities", ""),
        "entity_avg_allocation_pct": diagnostics.get("entity_avg_allocation_pct"),
        "entity_median_allocation_pct": diagnostics.get("entity_median_allocation_pct"),
        "entity_p10_allocation_pct": diagnostics.get("entity_p10_allocation_pct"),
        "ledger_dir": ledger_dir,
    }


def write_comparison(rows: list[dict], out_dir: Path) -> None:
    fields = [
        "marker", "period", "start", "end", "variant", "selection_metric", "min_trades_for_turnover",
        "return_pct", "mdd_pct", "return_mdd", "trade_count", "buy_count", "sell_count", "final_equity",
        "elapsed_seconds", "avg_open_entity_positions", "max_open_entity_positions", "ticker_cap_hit_events",
        "ticker_cap_hit_tickers", "turnover_excluded_min_trades", "turnover_scored_entities",
        "entity_avg_allocation_pct", "entity_median_allocation_pct", "entity_p10_allocation_pct", "ledger_dir",
    ]
    with (out_dir / "comparison.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _fmt(value, digits=2):
    if value == "" or value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def write_report(summary: dict, out_dir: Path) -> None:
    rows = summary["rows"]
    lines = [
        f"# Entity Turnover Backtest ({MARKER})",
        "",
        "이 산출물은 batch 완료 전 533 pool 기준 잠정 결과입니다. 절대 수익률은 OOS-aware pool 누수 때문에 신뢰하지 말고, turnover_score와 confidence_adjusted의 상대 비교 참고용으로만 사용합니다.",
        "",
        "## Configuration",
        "",
        f"- pool raw entities: {summary['config']['raw_entities']}",
        f"- eligible entities after turnover tag cache: {summary['config']['eligible_entities']}",
        f"- missing turnover entities: {summary['turnover_tag_meta']['missing_turnover_entities']}",
        "- allow_same_ticker_entities: true",
        "- max_positions: 8 entity slots",
        "- per_ticker_exposure_cap: 25% aggregated by ticker",
        "- variants: confidence_adjusted, turnover_score min_trades 5/10/15",
        "",
        "## Comparison",
        "",
        "| Period | Variant | Return % | MDD % | Return/MDD | Trades | Avg Open | Max Open | Cap Hits | Turnover Excluded | Seconds |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    order = {"stress": 0, "mid": 1, "oos": 2}
    for row in sorted(rows, key=lambda r: (order.get(r["period"], 99), r["variant"])):
        lines.append(
            "| {period} | {variant} | {ret} | {mdd} | {rmdd} | {trades} | {avg_open} | {max_open} | {cap_hits} | {excluded} | {seconds} |".format(
                period=row["period"],
                variant=row["variant"],
                ret=_fmt(row["return_pct"], 2),
                mdd=_fmt(row["mdd_pct"], 2),
                rmdd=_fmt(row["return_mdd"], 2),
                trades=row["trade_count"],
                avg_open=_fmt(row["avg_open_entity_positions"], 2),
                max_open=row["max_open_entity_positions"],
                cap_hits=row["ticker_cap_hit_events"],
                excluded=row["turnover_excluded_min_trades"],
                seconds=_fmt(row["elapsed_seconds"], 1),
            )
        )
    lines.extend([
        "",
        "## Provisional caveats",
        "",
        "- This run uses the current 533 entity pool before the long batch has completed.",
        "- Entities missing turnover tags are excluded before all variants so comparison is on the same eligible universe.",
        "- Signal caches are generated once per period and reused by all variants in that period.",
    ])
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--marker", default=MARKER)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"

    def log(message: str) -> None:
        line = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {message}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    start_ts = time.time()
    log(f"START {MARKER}")
    raw_entities = load_pool_entities()
    log(f"loaded raw_entities={len(raw_entities)} tickers={len({normalize_ticker(e.ticker) for e in raw_entities})}")
    tag_t0 = time.perf_counter()
    tag_cache, tag_meta = build_turnover_tag_cache(raw_entities, out_dir)
    eligible_entities, missing = apply_turnover_tags(raw_entities, tag_cache)
    tag_seconds = time.perf_counter() - tag_t0
    log(f"turnover tag cache done eligible={len(eligible_entities)} missing={len(missing)} seconds={tag_seconds:.2f}")

    rows = []
    signal_cache_meta = {}
    for period_name, (period_start, period_end) in PERIODS.items():
        log(f"build signal cache period={period_name} {period_start}..{period_end}")
        cache_t0 = time.perf_counter()
        signal_cache = build_signal_cache(eligible_entities, period_start, period_end)
        cache_seconds = time.perf_counter() - cache_t0
        signal_path = out_dir / f"signal_cache_{period_name}.json"
        serialize_signal_cache(signal_cache, signal_path)
        signal_cache_meta[period_name] = {
            "start": period_start,
            "end": period_end,
            "dates": len(signal_cache),
            "signals": sum(len(items) for items in signal_cache.values()),
            "seconds": cache_seconds,
            "path": str(signal_path),
        }
        log(f"signal cache done period={period_name} dates={len(signal_cache)} signals={sum(len(items) for items in signal_cache.values())} seconds={cache_seconds:.2f}")
        variants = [
            ("confidence_adjusted", "confidence", 30),
            ("turnover_score_mt5", "turnover_score", 5),
            ("turnover_score_mt10", "turnover_score", 10),
            ("turnover_score_mt15", "turnover_score", 15),
        ]
        for variant, metric, min_trades in variants:
            log(f"run variant period={period_name} variant={variant}")
            row = run_variant(
                period_name=period_name,
                start=period_start,
                end=period_end,
                variant=variant,
                entities=eligible_entities,
                signal_cache=signal_cache,
                selection_metric=metric,
                min_trades_for_turnover=min_trades,
                out_dir=out_dir,
            )
            rows.append(row)
            log(f"done period={period_name} variant={variant} return={_fmt(row['return_pct'], 3)} mdd={_fmt(row['mdd_pct'], 3)} trades={row['trade_count']} seconds={_fmt(row['elapsed_seconds'], 2)}")

    summary = {
        "marker": MARKER,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_seconds": time.time() - start_ts,
        "config": {
            "raw_entities": len(raw_entities),
            "eligible_entities": len(eligible_entities),
            "eligible_tickers": len({normalize_ticker(e.ticker) for e in eligible_entities}),
            "allow_same_ticker_entities": True,
            "max_positions": 8,
            "per_ticker_exposure_cap": 0.25,
            "position_sizing": "score_weighted",
            "confidence_mode": "adjusted_pf_cap10_min15_neutral",
            "periods": PERIODS,
            "variants": ["confidence_adjusted", "turnover_score_mt5", "turnover_score_mt10", "turnover_score_mt15"],
        },
        "turnover_tag_meta": tag_meta,
        "signal_cache_meta": signal_cache_meta,
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_comparison(rows, out_dir)
    write_report(summary, out_dir)
    log(f"WROTE summary={out_dir / 'summary.json'} comparison={out_dir / 'comparison.csv'} report={out_dir / 'report.md'}")
    log("END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
