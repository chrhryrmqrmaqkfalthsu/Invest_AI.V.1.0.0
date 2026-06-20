"""Stage2 survivor grid18 multisegment robust runner.

This runner is intentionally separate from ``run_policy_search``: it performs a
single continuous OOS backtest per combo, then recomputes monthly/quarterly
robust scores from the resulting equity curve.
"""
from __future__ import annotations

import json
import resource
import statistics
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

import engine.central.backtester as backtester_mod
from engine.central.allocation_policy import AllocationParams
from engine.central.backtester import run_central_backtest
from engine.central.policy_search import apply_confidence_metric, recompute_multiseg_robust, robust_score_from_returns
from engine.central.signal_collector import CacheOnlyDataProvider, SignalCollector
from engine.central.stage2_survivor_loader import load_stage2_survivors_with_report, sell_omen_coverage_report

BATCH_ROOT = Path("exp_batch_stage123_2009_20260616_full")
CENTRAL_INDEX = BATCH_ROOT / "central_index.jsonl"
OUT_DIR = Path("data/_system/central/stage2_b/grid18_multiseg")
OUT_JSON = OUT_DIR / "grid18_multiseg_20260620_result.json"
START = "2025-07-01"
END = "2026-06-15"
CAPITAL = 10_000.0
STRICT_HINT = {"FCFS", "CBOE", "HCC", "FIX", "COKE", "GTX", "CPRX", "AZO", "CW", "FRO", "GME", "AMR", "ERX", "BVN"}
SIGNAL_CACHE: dict[str, list[Any]] = {}


class PreloadedProvider:
    def __init__(self, frames: dict[str, Any]) -> None:
        self.frames = frames
        self.sell_omen_guard_violations = 0
        self.sell_omen_missing_tickers = set()
        self.sell_omen_loaded_rows = 0

    def load_price_df(self, ticker: str):
        return self.frames[str(ticker).upper()]

    def load_market_history(self):
        return None

    def load_ticker_sentiment(self, ticker: str):
        return {}


class CachedSignalCollector:
    confidence_by_entity: dict[str, float] = {}

    def __init__(self, provider, use_llm_events: bool = False) -> None:
        self.provider = provider
        self.use_llm_events = use_llm_events

    def collect(self, entities, date):
        key = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)[:10]
        rows = SIGNAL_CACHE.get(key) or []
        wanted = {entity.entity_id for entity in entities}
        output = []
        for row in rows:
            if row.entity_id not in wanted:
                continue
            confidence = float(self.confidence_by_entity.get(row.entity_id, row.confidence))
            output.append(replace(row, confidence=confidence))
        return output


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    log("GRID18_MULTISEG_START", {"time": started_at})

    load_report = load_stage2_survivors_with_report(CENTRAL_INDEX, BATCH_ROOT)
    entities_all = load_report.entities
    coverage = sell_omen_coverage_report(entities_all)
    input_payload = {
        "central_index_rows": load_report.central_index_rows,
        "stage2_survivor_rows": load_report.stage2_survivor_rows,
        "loaded_entities": load_report.loaded,
        "unique_tickers": len({entity.ticker for entity in entities_all}),
        "missing_source_files": load_report.missing_source_files,
        "unmatched_rulebook_hashes": load_report.unmatched_rulebook_hashes,
        "skipped_hash_mismatch": load_report.skipped_hash_mismatch,
        "sell_omen_off": True,
        "sell_omen_lr8d85_covered": coverage.covered,
        "sell_omen_lr8d85_missing": coverage.missing,
    }
    log("INPUT", input_payload)

    frames, cache_payload = load_oos_frames(entities_all)
    log("CACHE", cache_payload)
    if cache_payload["usable_oos_tickers"] / max(1, cache_payload["unique_tickers_total"]) < 0.90:
        raise RuntimeError("too many missing OOS ticker caches")
    usable_tickers = set(frames)
    entities = [entity for entity in entities_all if entity.ticker in usable_tickers]

    cache_signals(frames, entities)
    combos = grid18_combos()

    original_collector = backtester_mod.SignalCollector
    backtester_mod.SignalCollector = CachedSignalCollector
    rows: list[dict[str, Any]] = []
    selection_counts: Counter[str] = Counter()
    selection_buy_counts: Counter[str] = Counter()
    selection_pnl: defaultdict[str, float] = defaultdict(float)
    combo_entity_counts: list[dict[str, Any]] = []
    start_all = time.perf_counter()
    try:
        for idx, combo in enumerate(combos, start=1):
            row = run_combo(idx, combo, entities, frames)
            rows.append(row)
            for entity_id, count in row.pop("_entity_trade_counts").items():
                selection_counts[entity_id] += count["combo_presence"]
                selection_buy_counts[entity_id] += count["buy_count"]
            for entity_id, pnl in row.pop("_entity_pnl").items():
                selection_pnl[entity_id] += pnl
            combo_entity_counts.append(row["combo_entity_count"])
            log(
                "COMBO_DONE",
                {
                    "combo": row["combo"],
                    "confidence_metric": row["confidence_metric"],
                    "max_positions": row["max_positions"],
                    "position_sizing": row["position_sizing"],
                    "single_oos_robust_score": row["single_oos"]["robust_score"],
                    "monthly_robust_score": row["monthly"]["robust_score"],
                    "quarterly_robust_score": row["quarterly"]["robust_score"],
                    "total_return": row["single_oos"]["total_return"],
                    "max_drawdown_pct": row["single_oos"]["max_drawdown_pct"],
                    "trades": row["single_oos"]["trades"],
                    "reconcile_failures": row["single_oos"]["reconcile_failures"],
                    "elapsed_sec": row["elapsed_sec"],
                },
            )
    finally:
        backtester_mod.SignalCollector = original_collector

    ranked_single = rank_rows(rows, key="single_oos")
    ranked_monthly = rank_rows(rows, key="monthly")
    ranked_quarterly = rank_rows(rows, key="quarterly")
    result_payload = build_result_payload(
        rows=rows,
        ranked_single=ranked_single,
        ranked_monthly=ranked_monthly,
        ranked_quarterly=ranked_quarterly,
        load_report=load_report,
        entities_all=entities_all,
        entities=entities,
        cache_payload=cache_payload,
        combo_entity_counts=combo_entity_counts,
        selection_counts=selection_counts,
        selection_buy_counts=selection_buy_counts,
        selection_pnl=selection_pnl,
        elapsed_sec=time.perf_counter() - start_all,
        started_at=started_at,
    )
    OUT_JSON.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    log("RESULT_JSON_PATH", str(OUT_JSON))
    log("GRID18_MULTISEG_DONE", {"time": time.strftime("%Y-%m-%d %H:%M:%S")})
    return 0


def load_oos_frames(entities_all) -> tuple[dict[str, Any], dict[str, Any]]:
    provider = CacheOnlyDataProvider(recompute_indicators=True, sell_omen_score_path=None)
    frames = {}
    missing = []
    no_oos = []
    for ticker in sorted({entity.ticker for entity in entities_all}):
        try:
            df = provider.load_price_df(ticker)
        except Exception as exc:  # pragma: no cover - diagnostic path for production data
            missing.append({"ticker": ticker, "error": type(exc).__name__, "message": str(exc)[:160]})
            continue
        oos = df[df.index >= START]
        if oos.empty:
            no_oos.append({"ticker": ticker, "start": str(df.index.min().date()), "end": str(df.index.max().date())})
            continue
        frames[ticker] = df
    payload = {
        "unique_tickers_total": len({entity.ticker for entity in entities_all}),
        "usable_oos_tickers": len(frames),
        "missing_tickers": len(missing),
        "no_oos_tickers": len(no_oos),
        "missing_sample": missing[:20],
        "no_oos_sample": no_oos[:20],
    }
    return frames, payload


def cache_signals(frames: dict[str, Any], entities) -> None:
    signal_t0 = time.perf_counter()
    collector = SignalCollector(PreloadedProvider(frames), use_llm_events=False)
    days = sorted({idx.normalize() for df in frames.values() for idx in df.index if START <= idx.strftime("%Y-%m-%d") <= END})
    signal_entities = apply_confidence_metric(entities, "expectancy")
    for i, day in enumerate(days, start=1):
        SIGNAL_CACHE[day.strftime("%Y-%m-%d")] = collector.collect(signal_entities, day)
        if i % 40 == 0 or i == len(days):
            log("SIGNALS_CACHED", {"days_done": i, "days_total": len(days), "elapsed_sec": round(time.perf_counter() - signal_t0, 2)})
    log("SIGNAL_CACHE_DONE", {"days": len(days), "elapsed_sec": round(time.perf_counter() - signal_t0, 2)})


def grid18_combos() -> list[dict[str, Any]]:
    combos = []
    for metric in ["expectancy", "win_rate", "profit_factor"]:
        for max_positions in [2, 3, 5]:
            for sizing in ["equal", "score_weighted"]:
                combos.append({"confidence_metric": metric, "max_positions": max_positions, "position_sizing": sizing})
    return combos


def run_combo(idx: int, combo: dict[str, Any], entities, frames: dict[str, Any]) -> dict[str, Any]:
    adjusted = apply_confidence_metric(entities, combo["confidence_metric"])
    CachedSignalCollector.confidence_by_entity = {entity.entity_id: entity.confidence for entity in adjusted}
    alloc = AllocationParams(
        max_positions=int(combo["max_positions"]),
        confidence_weight=0.5,
        signal_strength_weight=0.5,
        min_confidence=0.0,
        total_capital=CAPITAL,
        per_ticker_exposure_cap=0.25,
        position_sizing=str(combo["position_sizing"]),
        cash_buffer_ratio=0.98,
    )
    t0 = time.perf_counter()
    result = run_central_backtest(
        adjusted,
        START,
        END,
        alloc,
        data_provider=PreloadedProvider(frames),
        ledger_dir=str(OUT_DIR / f"memory_combo_{idx:02d}"),
        persist_ledger=False,
        flush_ledger_on_finish=False,
    )
    elapsed = time.perf_counter() - t0
    single_robust = robust_score_from_returns(
        [float(result.total_return or 0.0)],
        max_drawdown_pct=float(result.max_drawdown_pct or 0.0),
        trades=len(result.trades),
    )
    multiseg = recompute_multiseg_robust(result, initial_equity=CAPITAL, granularities=("monthly", "quarterly"))
    entity_buy_counts = Counter(trade.entity_id for trade in result.trades if trade.side == "buy")
    entity_trade_counts = {entity_id: {"combo_presence": 1, "buy_count": count} for entity_id, count in entity_buy_counts.items()}
    ticker_buy_counts = Counter(trade.ticker for trade in result.trades if trade.side == "buy")
    sell_reasons = Counter(trade.reason for trade in result.trades if trade.side == "sell")
    return {
        "combo": idx,
        **combo,
        "confidence_weight": 0.5,
        "signal_strength_weight": 0.5,
        "min_confidence": 0.0,
        "elapsed_sec": elapsed,
        "single_oos": {
            "robust_score": float(single_robust),
            "mean_return": float(result.total_return or 0.0),
            "worst_segment_return": float(result.total_return or 0.0),
            "return_stdev": 0.0,
            "total_return": float(result.total_return or 0.0),
            "max_drawdown_pct": float(result.max_drawdown_pct or 0.0),
            "trades": len(result.trades),
            "buy_trades": sum(1 for trade in result.trades if trade.side == "buy"),
            "sell_trades": sum(1 for trade in result.trades if trade.side == "sell"),
            "rejected_backtester": int(getattr(result, "rejected_order_count", 0) or 0),
            "reconcile_failures": len(result.reconcile_failures),
            "final_equity": float(result.final_equity or 0.0),
            "open_positions_last": result.equity_curve[-1].open_position_count if result.equity_curve else 0,
            "sell_reasons": dict(sell_reasons),
            "unique_buy_entities": len(entity_buy_counts),
            "top_buy_tickers": ticker_buy_counts.most_common(10),
        },
        "monthly": multiseg["monthly"],
        "quarterly": multiseg["quarterly"],
        "combo_entity_count": {"combo": idx, "unique_buy_entities": len(entity_buy_counts), "top_buy_tickers": ticker_buy_counts.most_common(10)},
        "_entity_trade_counts": entity_trade_counts,
        "_entity_pnl": dict(result.per_entity_pnl),
    }


def rank_rows(rows: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            row[key]["robust_score"],
            row[key]["mean_return"],
            -abs(row[key]["max_drawdown_pct"]),
            row[key]["trades"],
        ),
        reverse=True,
    )
    output = []
    for rank, row in enumerate(ranked, start=1):
        slim = combo_slim(row, key=key)
        slim["rank"] = rank
        output.append(slim)
    return output


def combo_slim(row: dict[str, Any], *, key: str) -> dict[str, Any]:
    metrics = row[key]
    return {
        "combo": row["combo"],
        "confidence_metric": row["confidence_metric"],
        "max_positions": row["max_positions"],
        "position_sizing": row["position_sizing"],
        "confidence_weight": row["confidence_weight"],
        "signal_strength_weight": row["signal_strength_weight"],
        "min_confidence": row["min_confidence"],
        "robust_score": metrics["robust_score"],
        "mean_return": metrics["mean_return"],
        "worst_segment_return": metrics["worst_segment_return"],
        "best_segment_return": metrics.get("best_segment_return", metrics["mean_return"]),
        "return_stdev": metrics["return_stdev"],
        "total_return": metrics["total_return"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "trades": metrics["trades"],
        "reconcile_failures": row["single_oos"]["reconcile_failures"],
        "rejected_backtester": row["single_oos"]["rejected_backtester"],
        "segment_count": metrics.get("segment_count", 1),
        "negative_segment_count": metrics.get("negative_segment_count", 0),
        "best_segment_to_total_ratio": metrics.get("best_segment_to_total_ratio"),
    }


def build_result_payload(**kwargs) -> dict[str, Any]:
    rows = kwargs["rows"]
    entities = kwargs["entities"]
    entities_all = kwargs["entities_all"]
    load_report = kwargs["load_report"]
    robust_values = [row["monthly"]["robust_score"] for row in rows]
    entity_lookup = {entity.entity_id: entity for entity in entities}
    entity_freq_rows = []
    for entity_id, combo_count in kwargs["selection_counts"].most_common(50):
        entity = entity_lookup.get(entity_id)
        ticker = entity_id.split("_")[0]
        entity_freq_rows.append(
            {
                "entity_id": entity_id,
                "ticker": ticker,
                "rulebook_hash": getattr(entity, "rulebook_hash", "") if entity else "",
                "combo_count": combo_count,
                "buy_count": kwargs["selection_buy_counts"][entity_id],
                "pnl_sum": kwargs["selection_pnl"][entity_id],
                "strict_hint": ticker in STRICT_HINT,
            }
        )
    ticker_freq = Counter()
    ticker_buy = Counter()
    for row in entity_freq_rows:
        ticker_freq[row["ticker"]] += row["combo_count"]
        ticker_buy[row["ticker"]] += row["buy_count"]
    ticker_freq_rows = [
        {"ticker": ticker, "combo_count_sum": combo_count, "buy_count_sum": ticker_buy[ticker], "strict_hint": ticker in STRICT_HINT}
        for ticker, combo_count in ticker_freq.most_common(50)
    ]
    return {
        "run_meta": {
            "run_id": "stage2_b_grid18_multiseg_20260620",
            "created_at_local": time.strftime("%Y-%m-%d %H:%M:%S"),
            "started_at_local": kwargs["started_at"],
            "branch": "ml-sell-omen-20260608",
            "input_central_index_rows": load_report.central_index_rows,
            "input_stage2_survivor_rows": load_report.stage2_survivor_rows,
            "input_loaded_entities": len(entities_all),
            "used_entities": len(entities),
            "unique_tickers": len({entity.ticker for entity in entities}),
            "excluded_entities": len(entities_all) - len(entities),
            "sell_omen": "OFF",
            "eval_period": {"label": "oos_2025h2", "start": START, "end": END},
            "capital": CAPITAL,
            "grid_count": 18,
            "fixed": {"confidence_weight": 0.5, "signal_strength_weight": 0.5, "min_confidence": 0.0},
            "elapsed_sec_total": kwargs["elapsed_sec"],
            "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "signal_cache_days": len(SIGNAL_CACHE),
            "note": "single OOS backtest per combo; monthly/quarterly robust scores are equity_curve post-processing outputs",
        },
        "cache": kwargs["cache_payload"],
        "ranked_single_oos": kwargs["ranked_single"],
        "ranked_monthly": kwargs["ranked_monthly"],
        "ranked_quarterly": kwargs["ranked_quarterly"],
        "combos": rows,
        "monthly_robust_distribution": distribution(robust_values),
        "top_entities_by_combo_frequency": entity_freq_rows,
        "top_tickers_by_entity_frequency": ticker_freq_rows,
        "combo_entity_counts": kwargs["combo_entity_counts"],
        "limitations": [
            "oos_2025h2 was also used in Stage2 survivor filtering, so this is not a clean holdout.",
            "Stage2 exit rulebook only; Stage3 exit-profile validation is not included.",
            "sell_omen is disabled to avoid coverage-driven heterogeneity.",
            "confidence_weight, signal_strength_weight, and min_confidence are fixed; this is a reduced 18-combo policy search.",
            "Multi-segment splitting measures time consistency inside one selected OOS span; it does not prove future generalization.",
        ],
    }


def distribution(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "max": max(values),
        "range": max(values) - min(values),
        "mean": sum(values) / len(values),
        "median": statistics.median(values),
        "stdev": statistics.pstdev(values),
    }


def log(label: str, payload: Any = None) -> None:
    if payload is None:
        print(label, flush=True)
    else:
        print(label, json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
