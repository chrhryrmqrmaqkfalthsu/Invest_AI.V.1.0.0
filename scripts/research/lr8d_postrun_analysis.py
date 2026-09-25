#!/usr/bin/env python3
"""LR-8D A+B+C+D post-run analysis.

Usage:
    venv/bin/python scripts/research/lr8d_postrun_analysis.py

The script is intentionally read-only for RUN artifacts. It prints a compact
markdown report and also writes the same report next to the LR-8D outputs.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUN_ID = "lr8d_abcd_20260608"
RUN_PREFIX = "lr8d_abcd"
DEFAULT_RUN_DIR = Path("data/_system/research") / RUN_ID
DEFAULT_CONDITION_DIR = Path("data/_system/condition_db_sell_omen_clean")
EXPECTED_PERIOD_ROWS = 340
EXPECTED_SHARD_COUNT = 8
PREVIOUS_BALANCED_K2 = 50
PREVIOUS_STRICT_K3 = 24
GENERAL_YEARS = (2022, 2023, 2024)
STRESS_LABELS = ("2025H2",)
MIN_TRADES = 5
MIN_MEMBER_SCORE = 10.0

FALLBACK_MARKET_SCORE = 50.0
FALLBACK_VIX = 18.0
# 3구간 판정: 정상 / 주의 / 위험. 필요하면 여기만 조정하면 된다.
COVERAGE_OK_NAN_PCT = 0.1
COVERAGE_OK_FALLBACK_PCT = 1.0
COVERAGE_WARN_NAN_PCT = 2.0
COVERAGE_WARN_FALLBACK_PCT = 10.0


Number = int | float


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str) and not value.strip():
            return default
        v = float(value)
        if math.isnan(v):
            return default
        return v
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def pct(numer: Number, denom: Number) -> float:
    d = float(denom or 0)
    if d == 0:
        return 0.0
    return float(numer) / d * 100.0


def fmt(value: Any, digits: int = 3, suffix: str = "") -> str:
    if value is None:
        return "NA"
    try:
        v = float(value)
    except Exception:
        return str(value)
    if math.isnan(v):
        return "NA"
    return f"{v:.{digits}f}{suffix}"


def mean_or_none(values: Iterable[Any]) -> float | None:
    vals = [safe_float(v) for v in values if v is not None]
    return mean(vals) if vals else None


def median_or_none(values: Iterable[Any]) -> float | None:
    vals = [safe_float(v) for v in values if v is not None]
    return median(vals) if vals else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception as exc:
                raise RuntimeError(f"JSONL parse failed: {path}:{line_no}: {exc}") from exc
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception as exc:
                raise RuntimeError(f"JSONL parse failed: {path}:{line_no}: {exc}") from exc
            if isinstance(obj, dict):
                yield obj


def metric_from_candidate(candidate: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    oos = candidate.get("oos") if isinstance(candidate.get("oos"), Mapping) else {}
    metrics = candidate.get("oos_metrics") if isinstance(candidate.get("oos_metrics"), Mapping) else {}
    if key in metrics:
        return safe_float(metrics.get(key), default)
    if key in oos:
        return safe_float(oos.get(key), default)
    return safe_float(candidate.get(key), default)


def load_topn_validation_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    periods: list[dict[str, Any]] = []
    for row in rows:
        split = row.get("split") if isinstance(row.get("split"), Mapping) else {}
        periods.append(
            {
                "ticker": row.get("ticker"),
                "year": row.get("year"),
                "label": row.get("label"),
                "is_stress": row.get("is_stress"),
                "train_period": [split.get("train_start"), split.get("train_end")],
                "test_period": [split.get("test_start"), split.get("test_end")],
                "candidate_count": row.get("qualified_count"),
                "candidates": row.get("candidates", []),
            }
        )
    return {"method": "qualified_all_min_trades_member_score", "periods": periods}


def recompute_survivors(topn_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if not topn_rows:
        return [], "topn_empty"
    try:
        from engine.pipeline.topn_survivor import evaluate_survivors, score_topn_validation_periods
    except Exception as exc:
        return [], f"import_failed:{exc}"

    validation = load_topn_validation_from_rows(topn_rows)
    scored = score_topn_validation_periods(
        validation,
        general_years=GENERAL_YEARS,
        stress_labels=STRESS_LABELS,
    )
    out: list[dict[str, Any]] = []
    for survivor_k, combo_id in ((2, "balanced_k2"), (3, "strict_k3")):
        rows = evaluate_survivors(
            scored,
            survivor_k=survivor_k,
            min_trades=MIN_TRADES,
            min_member_score=MIN_MEMBER_SCORE,
        )
        for row in rows:
            enriched = dict(row)
            enriched["combo_id"] = combo_id
            out.append(enriched)
    return out, "recomputed_from_topn"


def load_survivors(run_dir: Path, prefix: str, topn_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    path = run_dir / f"{prefix}_survivors.jsonl"
    rows = read_jsonl(path)
    if rows:
        return rows, "file"
    return recompute_survivors(topn_rows)


def survivor_k(row: Mapping[str, Any]) -> int | None:
    combo = str(row.get("combo_id") or "")
    if "k2" in combo:
        return 2
    if "k3" in combo:
        return 3
    thresholds = row.get("thresholds") if isinstance(row.get("thresholds"), Mapping) else {}
    raw = thresholds.get("survivor_k")
    if raw is not None:
        return safe_int(raw)
    return None


def selected_hash(row: Mapping[str, Any]) -> str:
    return str(row.get("selected_rulebook_hash") or row.get("rulebook_hash") or "")


def selected_source(row: Mapping[str, Any]) -> str:
    value = row.get("selected_rulebook_source_label")
    if value is None:
        value = row.get("selected_rulebook_source_year")
    return str(value or "")


def build_rulebook_maps(rulebook_rows: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str, str], dict[str, Any]]]:
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_ticker_year_hash: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rulebook_rows:
        h = str(row.get("rulebook_hash") or "")
        ticker = str(row.get("ticker") or "")
        year = str(row.get("year") or row.get("label") or "")
        if h:
            by_hash[h].append(row)
            by_ticker_year_hash[(ticker, year, h)] = row
    return by_hash, by_ticker_year_hash


def pick_rulebook_for_survivor(
    survivor: Mapping[str, Any],
    by_hash: Mapping[str, list[dict[str, Any]]],
    by_ticker_year_hash: Mapping[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any] | None:
    h = selected_hash(survivor)
    if not h:
        return None
    ticker = str(survivor.get("ticker") or "")
    source = selected_source(survivor)
    if ticker and source and (ticker, source, h) in by_ticker_year_hash:
        return by_ticker_year_hash[(ticker, source, h)]
    candidates = by_hash.get(h) or []
    if ticker:
        same_ticker = [row for row in candidates if str(row.get("ticker") or "") == ticker]
        if same_ticker:
            return same_ticker[0]
    return candidates[0] if candidates else None


def rulebook_payload(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    rb = row.get("rulebook") if isinstance(row.get("rulebook"), Mapping) else None
    if rb is None:
        rb = row.get("rulebook_full") if isinstance(row.get("rulebook_full"), Mapping) else None
    return dict(rb or {})


def field_bool_distribution(rulebooks: list[dict[str, Any]], key: str) -> Counter:
    counter: Counter = Counter()
    for rb in rulebooks:
        b = as_bool(rb.get(key))
        if b is True:
            counter["true"] += 1
        elif b is False:
            counter["false"] += 1
        else:
            counter["missing"] += 1
    return counter


def news_weight_summary(rulebooks: list[dict[str, Any]]) -> dict[str, Any]:
    all_keys = sorted({k for rb in rulebooks for k in rb if k.startswith("weight_news_")})
    per_key: list[dict[str, Any]] = []
    any_nonzero = 0
    for rb in rulebooks:
        if any(abs(safe_float(rb.get(k), 0.0)) > 1e-12 for k in all_keys):
            any_nonzero += 1
    for key in all_keys:
        values = [safe_float(rb.get(key), 0.0) for rb in rulebooks if key in rb]
        nonzero = [v for v in values if abs(v) > 1e-12]
        per_key.append(
            {
                "key": key,
                "present": len(values),
                "nonzero": len(nonzero),
                "nonzero_pct": pct(len(nonzero), len(rulebooks)),
                "avg": mean(values) if values else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
            }
        )
    per_key.sort(key=lambda row: (row["nonzero"], abs(safe_float(row["avg"], 0.0))), reverse=True)
    return {
        "keys": all_keys,
        "any_nonzero": any_nonzero,
        "any_nonzero_pct": pct(any_nonzero, len(rulebooks)),
        "per_key": per_key,
    }


def ratio_distribution(rulebooks: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, float]] = []
    for rb in rulebooks:
        stop = rb.get("stop_loss_atr")
        take = rb.get("take_profit_atr")
        if stop is None or take is None:
            continue
        stop_f = safe_float(stop, None)  # type: ignore[arg-type]
        take_f = safe_float(take, None)  # type: ignore[arg-type]
        if stop_f is None or take_f is None or stop_f <= 0:
            continue
        rows.append({"stop": stop_f, "take": take_f, "take_stop_ratio": take_f / stop_f})
    inverted = [row for row in rows if row["take"] < row["stop"]]
    return {
        "n": len(rows),
        "inverted": len(inverted),
        "inverted_pct": pct(len(inverted), len(rows)),
        "stop_median": median_or_none(row["stop"] for row in rows),
        "take_median": median_or_none(row["take"] for row in rows),
        "take_stop_ratio_median": median_or_none(row["take_stop_ratio"] for row in rows),
        "stop_min": min((row["stop"] for row in rows), default=None),
        "stop_max": max((row["stop"] for row in rows), default=None),
        "take_min": min((row["take"] for row in rows), default=None),
        "take_max": max((row["take"] for row in rows), default=None),
    }


@dataclass
class TradeAgg:
    n: int = 0
    wins: int = 0
    pnl_pct_sum: float = 0.0
    pnl_krw_sum: float = 0.0
    mfe_sum: float = 0.0
    mae_sum: float = 0.0
    holding_days_sum: float = 0.0
    worst_mae: float | None = None
    pnl_by_date: list[tuple[str, float]] = field(default_factory=list)

    def add(self, trade: Mapping[str, Any]) -> None:
        pnl_pct = safe_float(trade.get("pnl_pct"), 0.0)
        pnl_krw = safe_float(trade.get("pnl_krw"), 0.0)
        mfe = safe_float(trade.get("max_profit_during_hold"), 0.0)
        mae = safe_float(trade.get("max_loss_during_hold"), 0.0)
        self.n += 1
        self.wins += 1 if pnl_pct > 0 else 0
        self.pnl_pct_sum += pnl_pct
        self.pnl_krw_sum += pnl_krw
        self.mfe_sum += mfe
        self.mae_sum += mae
        self.holding_days_sum += safe_float(trade.get("holding_days"), 0.0)
        self.worst_mae = mae if self.worst_mae is None else min(self.worst_mae, mae)
        date = str(trade.get("exit_date") or trade.get("entry_date") or "")
        if date:
            self.pnl_by_date.append((date, pnl_pct))

    def mdd_pct_points(self) -> float | None:
        if not self.pnl_by_date:
            return None
        total = 0.0
        peak = 0.0
        worst = 0.0
        for _, pnl in sorted(self.pnl_by_date, key=lambda x: x[0]):
            total += pnl
            peak = max(peak, total)
            worst = min(worst, total - peak)
        return worst

    def row(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "win_rate": pct(self.wins, self.n),
            "avg_pnl_pct": self.pnl_pct_sum / self.n if self.n else None,
            "sum_pnl_pct_points": self.pnl_pct_sum,
            "sum_pnl_krw": self.pnl_krw_sum,
            "avg_mfe_pct": self.mfe_sum / self.n if self.n else None,
            "avg_mae_pct": self.mae_sum / self.n if self.n else None,
            "worst_mae_pct": self.worst_mae,
            "avg_holding_days": self.holding_days_sum / self.n if self.n else None,
            "approx_mdd_pct_points": self.mdd_pct_points(),
        }


@dataclass
class SellOmenExitAgg:
    n: int = 0
    winners: int = 0
    mfe_values: list[float] = field(default_factory=list)
    pnl_values: list[float] = field(default_factory=list)
    givebacks: list[float] = field(default_factory=list)
    winner_early_cut: int = 0
    big_giveback: int = 0

    def add(self, trade: Mapping[str, Any]) -> None:
        pnl = safe_float(trade.get("pnl_pct"), 0.0)
        mfe = safe_float(trade.get("max_profit_during_hold"), 0.0)
        giveback = mfe - pnl
        self.n += 1
        self.winners += 1 if pnl > 0 else 0
        self.pnl_values.append(pnl)
        self.mfe_values.append(mfe)
        self.givebacks.append(giveback)
        if mfe > 0 and giveback > 0:
            self.winner_early_cut += 1
        if mfe >= 2.0 and giveback >= max(1.0, mfe * 0.5):
            self.big_giveback += 1

    def row(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "win_rate": pct(self.winners, self.n),
            "avg_exit_pnl_pct": mean_or_none(self.pnl_values),
            "median_exit_pnl_pct": median_or_none(self.pnl_values),
            "avg_mfe_pct": mean_or_none(self.mfe_values),
            "median_mfe_pct": median_or_none(self.mfe_values),
            "avg_giveback_pctp": mean_or_none(self.givebacks),
            "median_giveback_pctp": median_or_none(self.givebacks),
            "winner_early_cut_count": self.winner_early_cut,
            "winner_early_cut_pct": pct(self.winner_early_cut, self.n),
            "big_giveback_count": self.big_giveback,
            "big_giveback_pct": pct(self.big_giveback, self.n),
        }


@dataclass
class ShiftRobustAgg:
    sell_omen_exit_count: int = 0
    prev_score_available: int = 0
    prev_score_hits_threshold: int = 0
    exit_score_values: list[float] = field(default_factory=list)
    prev_score_values: list[float] = field(default_factory=list)
    thresholds: list[float] = field(default_factory=list)

    def add(self, trade: Mapping[str, Any]) -> None:
        self.sell_omen_exit_count += 1
        threshold = safe_float(trade.get("sell_omen_threshold"), 0.0)
        self.thresholds.append(threshold)
        exit_score = trade.get("sell_omen_score")
        if exit_score is None:
            exit_ctx = trade.get("exit_context_full") if isinstance(trade.get("exit_context_full"), Mapping) else {}
            inputs = exit_ctx.get("inputs") if isinstance(exit_ctx.get("inputs"), Mapping) else {}
            exit_score = inputs.get("sell_omen_score")
        if exit_score is not None:
            self.exit_score_values.append(safe_float(exit_score, 0.0))

        holding = trade.get("holding_path_full") if isinstance(trade.get("holding_path_full"), list) else []
        if len(holding) < 2:
            return
        prev = holding[-2]
        cols = prev.get("columns") if isinstance(prev, Mapping) and isinstance(prev.get("columns"), Mapping) else {}
        prev_score = cols.get("sell_omen_score")
        if prev_score is None:
            return
        prev_score_f = safe_float(prev_score, None)  # type: ignore[arg-type]
        if prev_score_f is None:
            return
        self.prev_score_available += 1
        self.prev_score_values.append(prev_score_f)
        if threshold and prev_score_f >= threshold:
            self.prev_score_hits_threshold += 1

    def row(self) -> dict[str, Any]:
        return {
            "sell_omen_exit_count": self.sell_omen_exit_count,
            "prev_score_available": self.prev_score_available,
            "prev_score_hit_count": self.prev_score_hits_threshold,
            "prev_score_hit_pct_of_available": pct(self.prev_score_hits_threshold, self.prev_score_available),
            "prev_score_hit_pct_of_sell_omen_exits": pct(self.prev_score_hits_threshold, self.sell_omen_exit_count),
            "avg_threshold": mean_or_none(self.thresholds),
            "avg_exit_score": mean_or_none(self.exit_score_values),
            "avg_prev_score": mean_or_none(self.prev_score_values),
        }


def is_sell_omen_exit(trade: Mapping[str, Any]) -> bool:
    return "sell_omen" in str(trade.get("exit_reason") or "").lower()


def scan_trades(path: Path, selected_hashes: set[str], survivor_tickers: set[str]) -> dict[str, Any]:
    groups: dict[str, TradeAgg] = defaultdict(TradeAgg)
    sell_omen_exit = SellOmenExitAgg()
    shift = ShiftRobustAgg()
    period_line_count = 0
    trade_count_observed = 0
    trade_count_declared = 0
    keys: set[tuple[str, str, str, str]] = set()
    selected_trade_count = 0
    survivor_ticker_trade_count = 0
    missing_field_counts: Counter = Counter()
    exit_reason_counts: Counter = Counter()

    required_trade_keys = (
        "exit_reason",
        "pnl_pct",
        "pnl_krw",
        "breakeven_enabled",
        "sell_omen_enabled",
        "sell_omen_threshold",
        "max_profit_during_hold",
        "max_loss_during_hold",
        "holding_days",
        "entry_signal_components",
    )

    for row in iter_jsonl(path) or []:
        period_line_count += 1
        ticker = str(row.get("ticker") or "")
        label = str(row.get("label") or row.get("year") or "")
        rank = str(row.get("rank_is") or "")
        rb_hash = str(row.get("rulebook_hash") or "")
        keys.add((ticker, label, rank, rb_hash))
        trades = row.get("trades") if isinstance(row.get("trades"), list) else []
        trade_count_declared += safe_int(row.get("trade_count"), 0)
        trade_count_observed += len(trades)
        for trade in trades:
            if not isinstance(trade, Mapping):
                continue
            for key in required_trade_keys:
                if key not in trade:
                    missing_field_counts[key] += 1
            rb_hash_t = str(trade.get("rulebook_hash") or rb_hash)
            ticker_t = ticker
            if rb_hash_t in selected_hashes:
                selected_trade_count += 1
            if ticker_t in survivor_tickers:
                survivor_ticker_trade_count += 1

            groups["all"].add(trade)
            enabled = as_bool(trade.get("sell_omen_enabled"))
            if enabled is True:
                groups["sell_omen_enabled_true"].add(trade)
            elif enabled is False:
                groups["sell_omen_enabled_false"].add(trade)
            else:
                groups["sell_omen_enabled_missing"].add(trade)
            be = as_bool(trade.get("breakeven_enabled"))
            if be is True:
                groups["breakeven_true"].add(trade)
            elif be is False:
                groups["breakeven_false"].add(trade)
            if rb_hash_t in selected_hashes:
                groups["selected_rulebooks"].add(trade)
            if ticker_t in survivor_tickers:
                groups["survivor_tickers"].add(trade)

            reason = str(trade.get("exit_reason") or "missing")
            exit_reason_counts[reason] += 1
            if is_sell_omen_exit(trade):
                groups["exit_sell_omen"].add(trade)
                sell_omen_exit.add(trade)
                shift.add(trade)
            else:
                groups["exit_not_sell_omen"].add(trade)

    return {
        "period_line_count": period_line_count,
        "trade_count_observed": trade_count_observed,
        "trade_count_declared": trade_count_declared,
        "keys": keys,
        "selected_trade_count": selected_trade_count,
        "survivor_ticker_trade_count": survivor_ticker_trade_count,
        "missing_field_counts": dict(missing_field_counts),
        "exit_reason_counts": exit_reason_counts,
        "groups": {name: agg.row() for name, agg in groups.items()},
        "sell_omen_exit": sell_omen_exit.row(),
        "shift": shift.row(),
    }


@dataclass
class CoverageBucket:
    rows: int = 0
    market_nan: int = 0
    market_fallback: int = 0
    vix_nan: int = 0
    vix_fallback: int = 0

    def add_market(self, value: Any) -> None:
        parsed = parse_csv_float(value)
        if parsed is None:
            self.market_nan += 1
        elif abs(parsed - FALLBACK_MARKET_SCORE) <= 1e-12:
            self.market_fallback += 1

    def add_vix(self, value: Any) -> None:
        parsed = parse_csv_float(value)
        if parsed is None:
            self.vix_nan += 1
        elif abs(parsed - FALLBACK_VIX) <= 1e-12:
            self.vix_fallback += 1

    def row(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "market_nan_pct": pct(self.market_nan, self.rows),
            "market_fallback_50_pct": pct(self.market_fallback, self.rows),
            "vix_nan_pct": pct(self.vix_nan, self.rows),
            "vix_fallback_18_pct": pct(self.vix_fallback, self.rows),
            "market_verdict": coverage_verdict(pct(self.market_nan, self.rows), pct(self.market_fallback, self.rows)),
            "vix_verdict": coverage_verdict(pct(self.vix_nan, self.rows), pct(self.vix_fallback, self.rows)),
        }


def parse_csv_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "na", "n/a"}:
        return None
    try:
        v = float(text)
        if math.isnan(v):
            return None
        return v
    except Exception:
        return None


def coverage_verdict(nan_pct: float, fallback_pct: float) -> str:
    if nan_pct <= COVERAGE_OK_NAN_PCT and fallback_pct <= COVERAGE_OK_FALLBACK_PCT:
        return "정상"
    if nan_pct <= COVERAGE_WARN_NAN_PCT and fallback_pct <= COVERAGE_WARN_FALLBACK_PCT:
        return "주의"
    return "위험"


def analyze_condition_coverage(condition_dir: Path) -> dict[str, Any]:
    paths = sorted(Path(p) for p in glob.glob(str(condition_dir / "*.csv")))
    overall = CoverageBucket()
    by_year: dict[str, CoverageBucket] = defaultdict(CoverageBucket)
    files_seen = 0
    missing_columns: list[str] = []

    for path in paths:
        files_seen += 1
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                continue
            fields = {name.lower(): name for name in reader.fieldnames}
            date_col = fields.get("date") or fields.get("datetime")
            market_col = fields.get("market_score")
            vix_col = fields.get("vix") or fields.get("vix_level")
            if not date_col or not market_col or not vix_col:
                missing_columns.append(str(path))
                continue
            for row in reader:
                date = str(row.get(date_col) or "")
                m = re.match(r"(\d{4})", date)
                year = m.group(1) if m else "unknown"
                overall.rows += 1
                by_year[year].rows += 1
                overall.add_market(row.get(market_col))
                overall.add_vix(row.get(vix_col))
                by_year[year].add_market(row.get(market_col))
                by_year[year].add_vix(row.get(vix_col))

    return {
        "files_seen": files_seen,
        "missing_columns": missing_columns,
        "overall": overall.row(),
        "by_year": {year: bucket.row() for year, bucket in sorted(by_year.items())},
    }


def analyze_integrity(
    run_dir: Path,
    prefix: str,
    topn_rows: list[dict[str, Any]],
    rulebook_rows: list[dict[str, Any]],
    trade_scan: dict[str, Any] | None,
    expected_period_rows: int,
) -> dict[str, Any]:
    labels = Counter(str(r.get("label") or r.get("year") or "") for r in topn_rows)
    tickers = {str(r.get("ticker") or "") for r in topn_rows if r.get("ticker")}
    candidate_total = sum(len(r.get("candidates") or []) for r in topn_rows)
    topn_keys = {
        (
            str(r.get("ticker") or ""),
            str(r.get("label") or r.get("year") or ""),
            str(c.get("rank_is") or ""),
            str(c.get("rulebook_hash") or ""),
        )
        for r in topn_rows
        for c in (r.get("candidates") or [])
        if isinstance(c, Mapping)
    }
    rulebook_keys = {
        (
            str(r.get("ticker") or ""),
            str(r.get("year") or r.get("label") or ""),
            str(r.get("rank_is") or ""),
            str(r.get("rulebook_hash") or ""),
        )
        for r in rulebook_rows
    }
    trade_keys = trade_scan.get("keys", set()) if trade_scan else set()
    shard_logs = sorted(run_dir.glob(f"{prefix}_shard_*.log"))
    shard_pids = sorted(run_dir.glob(f"{prefix}_shard_*.pid"))
    log_error_hits: list[str] = []
    for log_path in shard_logs:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")[-20000:]
        except Exception:
            continue
        hits = re.findall(r"(?im)(traceback|exception|error|failed)", text)
        if hits:
            log_error_hits.append(f"{log_path.name}:{len(hits)}")

    return {
        "topn_rows": len(topn_rows),
        "expected_period_rows": expected_period_rows,
        "topn_complete": len(topn_rows) == expected_period_rows,
        "ticker_count": len(tickers),
        "labels": dict(labels),
        "candidate_total_from_topn": candidate_total,
        "rulebook_rows": len(rulebook_rows),
        "rulebook_sync_missing_from_rulebook": len(topn_keys - rulebook_keys),
        "rulebook_sync_extra_rulebook": len(rulebook_keys - topn_keys),
        "trade_period_lines": trade_scan.get("period_line_count") if trade_scan else None,
        "trade_count_observed": trade_scan.get("trade_count_observed") if trade_scan else None,
        "trade_count_declared": trade_scan.get("trade_count_declared") if trade_scan else None,
        "trade_sync_missing_from_trades": len(topn_keys - trade_keys) if trade_scan else None,
        "trade_sync_extra_trades": len(trade_keys - topn_keys) if trade_scan else None,
        "shard_logs": len(shard_logs),
        "expected_shard_logs": EXPECTED_SHARD_COUNT,
        "shard_pids": len(shard_pids),
        "log_error_hits_tail_scan": log_error_hits[:20],
        "missing_trade_fields": trade_scan.get("missing_field_counts") if trade_scan else None,
    }


def oos_2024_2026_summary(topn_rows: list[dict[str, Any]], survivor_tickers: set[str]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for period in topn_rows:
        ticker = str(period.get("ticker") or "")
        if survivor_tickers and ticker not in survivor_tickers:
            continue
        year_raw = period.get("year")
        label = str(period.get("label") or year_raw or "")
        try:
            year_int = int(year_raw)
        except Exception:
            year_int = None
        include = False
        if year_int is not None and 2024 <= year_int <= 2026:
            include = True
        if label.startswith("2024") or label.startswith("2025") or label.startswith("2026"):
            include = True
        if not include:
            continue
        candidates = [c for c in (period.get("candidates") or []) if isinstance(c, Mapping)]
        if not candidates:
            continue
        best = max(candidates, key=lambda c: metric_from_candidate(c, "expectancy_pct", -9999.0))
        buckets[label].append(
            {
                "ticker": ticker,
                "expectancy_pct": metric_from_candidate(best, "expectancy_pct"),
                "profit_factor": metric_from_candidate(best, "profit_factor"),
                "win_rate": metric_from_candidate(best, "win_rate"),
                "trade_count": metric_from_candidate(best, "trade_count"),
                "max_drawdown_pct": metric_from_candidate(best, "max_drawdown_pct"),
            }
        )
    out: list[dict[str, Any]] = []
    for label, rows in sorted(buckets.items()):
        out.append(
            {
                "label": label,
                "ticker_count": len(rows),
                "avg_expectancy_pct": mean_or_none(row["expectancy_pct"] for row in rows),
                "median_expectancy_pct": median_or_none(row["expectancy_pct"] for row in rows),
                "min_expectancy_pct": min((safe_float(row["expectancy_pct"]) for row in rows), default=None),
                "avg_profit_factor": mean_or_none(row["profit_factor"] for row in rows),
                "avg_win_rate": mean_or_none(row["win_rate"] for row in rows),
                "avg_trade_count": mean_or_none(row["trade_count"] for row in rows),
                "worst_drawdown_pct": min((safe_float(row["max_drawdown_pct"]) for row in rows), default=None),
            }
        )
    return out


def table(headers: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "없음"
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def render_report(data: dict[str, Any]) -> str:
    integrity = data["integrity"]
    coverage = data.get("coverage")
    survivors = data["survivors"]
    survivor_source = data["survivor_source"]
    selected_rulebooks = data["selected_rulebooks"]
    all_rulebooks = data["all_rulebooks"]
    trade_scan = data.get("trade_scan")
    oos_rows = data["oos_2024_2026"]

    k_counts = Counter(survivor_k(row) for row in survivors)
    k2 = k_counts.get(2, 0)
    k3 = k_counts.get(3, 0)
    selected_count = len(selected_rulebooks)
    sell_omen_dist = field_bool_distribution(selected_rulebooks, "sell_omen_enabled")
    breakeven_dist = field_bool_distribution(selected_rulebooks, "breakeven_enabled")
    news = news_weight_summary(selected_rulebooks)
    selected_ratio = ratio_distribution(selected_rulebooks)
    all_ratio = ratio_distribution(all_rulebooks)

    lines: list[str] = []
    lines.append(f"# LR8D ABCD 포스트런 분석 — {RUN_ID}")
    lines.append("")
    lines.append("## Part 1 — 무결성")
    lines.append(
        table(
            ["항목", "값"],
            [
                ["topn rows / expected", f"{integrity['topn_rows']} / {integrity['expected_period_rows']}"],
                ["topn complete", integrity["topn_complete"]],
                ["ticker_count", integrity["ticker_count"]],
                ["labels", json.dumps(integrity["labels"], ensure_ascii=False, sort_keys=True)],
                ["candidate_total_from_topn", integrity["candidate_total_from_topn"]],
                ["rulebook_rows", integrity["rulebook_rows"]],
                ["rulebook missing/extra", f"{integrity['rulebook_sync_missing_from_rulebook']} / {integrity['rulebook_sync_extra_rulebook']}"],
                ["trade period lines", integrity["trade_period_lines"]],
                ["trade observed/declared", f"{integrity['trade_count_observed']} / {integrity['trade_count_declared']}"],
                ["trade missing/extra", f"{integrity['trade_sync_missing_from_trades']} / {integrity['trade_sync_extra_trades']}"],
                ["shard logs / expected", f"{integrity['shard_logs']} / {integrity['expected_shard_logs']}"],
                ["shard pid files", integrity["shard_pids"]],
                ["log tail error hits", json.dumps(integrity["log_error_hits_tail_scan"], ensure_ascii=False)],
            ],
        )
    )
    if integrity.get("missing_trade_fields"):
        lines.append("")
        lines.append("누락 trade 필드:")
        lines.append("```json")
        lines.append(json.dumps(integrity["missing_trade_fields"], ensure_ascii=False, indent=2, sort_keys=True))
        lines.append("```")

    lines.append("")
    lines.append("## Part 2 — coverage: market_score / vix")
    if coverage is None:
        lines.append("coverage 분석은 --skip-coverage 옵션 때문에 생략됨.")
    else:
        lines.append(f"condition_db csv files = {coverage['files_seen']}")
        overall = coverage["overall"]
        lines.append(
            table(
                ["범위", "rows", "market NaN", "market=50", "market 판정", "vix NaN", "vix=18", "vix 판정"],
                [[
                    "overall",
                    overall["rows"],
                    fmt(overall["market_nan_pct"], 3, "%"),
                    fmt(overall["market_fallback_50_pct"], 3, "%"),
                    overall["market_verdict"],
                    fmt(overall["vix_nan_pct"], 3, "%"),
                    fmt(overall["vix_fallback_18_pct"], 3, "%"),
                    overall["vix_verdict"],
                ]],
            )
        )
        year_rows = []
        for year, row in coverage["by_year"].items():
            year_rows.append([
                year,
                row["rows"],
                fmt(row["market_nan_pct"], 3, "%"),
                fmt(row["market_fallback_50_pct"], 3, "%"),
                row["market_verdict"],
                fmt(row["vix_nan_pct"], 3, "%"),
                fmt(row["vix_fallback_18_pct"], 3, "%"),
                row["vix_verdict"],
            ])
        lines.append("")
        lines.append(table(["연도", "rows", "market NaN", "market=50", "market 판정", "vix NaN", "vix=18", "vix 판정"], year_rows))
        if coverage.get("missing_columns"):
            lines.append("")
            lines.append("컬럼 누락 파일 일부:")
            lines.append("```text")
            lines.extend(coverage["missing_columns"][:20])
            lines.append("```")

    lines.append("")
    lines.append("## Part 3 — 포스트런 9개")
    lines.append("")
    lines.append("### 1) 생존자 수: K=2 / K=3")
    lines.append(
        table(
            ["구분", "현재", "이전 기준", "증감", "source"],
            [
                ["balanced K=2", k2, PREVIOUS_BALANCED_K2, k2 - PREVIOUS_BALANCED_K2, survivor_source],
                ["strict K=3", k3, PREVIOUS_STRICT_K3, k3 - PREVIOUS_STRICT_K3, survivor_source],
            ],
        )
    )

    lines.append("")
    lines.append("### 2) sell_omen_enabled 생존자 비율")
    lines.append(
        table(
            ["selected rulebooks", "true", "false", "missing", "true 비율"],
            [[
                selected_count,
                sell_omen_dist["true"],
                sell_omen_dist["false"],
                sell_omen_dist["missing"],
                fmt(pct(sell_omen_dist["true"], selected_count), 2, "%"),
            ]],
        )
    )

    lines.append("")
    lines.append("### 3) weight_news_* 비영 여부")
    lines.append(
        table(
            ["selected rulebooks", "weight_news_* keys", "any nonzero", "any nonzero 비율"],
            [[selected_count, len(news["keys"]), news["any_nonzero"], fmt(news["any_nonzero_pct"], 2, "%")]],
        )
    )
    news_rows = [
        [r["key"], r["present"], r["nonzero"], fmt(r["nonzero_pct"], 2, "%"), fmt(r["avg"], 4), fmt(r["min"], 4), fmt(r["max"], 4)]
        for r in news["per_key"][:12]
    ]
    lines.append(table(["key", "present", "nonzero", "nonzero%", "avg", "min", "max"], news_rows))

    lines.append("")
    lines.append("### 4) breakeven_enabled True/False 비율")
    lines.append(
        table(
            ["selected rulebooks", "true", "false", "missing", "true 비율"],
            [[
                selected_count,
                breakeven_dist["true"],
                breakeven_dist["false"],
                breakeven_dist["missing"],
                fmt(pct(breakeven_dist["true"], selected_count), 2, "%"),
            ]],
        )
    )

    lines.append("")
    lines.append("### 5) OOS 2024~2026 기대치")
    lines.append("survivor ticker별 해당 기간 best expectancy candidate 기준.")
    lines.append(
        table(
            ["label", "tickers", "avg exp", "median exp", "min exp", "avg PF", "avg WR", "avg trades", "worst DD"],
            [
                [
                    r["label"],
                    r["ticker_count"],
                    fmt(r["avg_expectancy_pct"], 4, "%"),
                    fmt(r["median_expectancy_pct"], 4, "%"),
                    fmt(r["min_expectancy_pct"], 4, "%"),
                    fmt(r["avg_profit_factor"], 4),
                    fmt(r["avg_win_rate"], 2, "%"),
                    fmt(r["avg_trade_count"], 2),
                    fmt(r["worst_drawdown_pct"], 4, "%"),
                ]
                for r in oos_rows
            ],
        )
    )

    lines.append("")
    lines.append("### 6) sell_omen 청산 MFE vs exit PnL — 승자 조기절단")
    if trade_scan:
        so = trade_scan["sell_omen_exit"]
        lines.append(
            table(
                ["sell_omen exits", "win_rate", "avg exit pnl", "avg MFE", "avg giveback", "winner early-cut", "big giveback"],
                [[
                    so["n"],
                    fmt(so["win_rate"], 2, "%"),
                    fmt(so["avg_exit_pnl_pct"], 4, "%"),
                    fmt(so["avg_mfe_pct"], 4, "%"),
                    fmt(so["avg_giveback_pctp"], 4, "pp"),
                    f"{so['winner_early_cut_count']} ({fmt(so['winner_early_cut_pct'], 2, '%')})",
                    f"{so['big_giveback_count']} ({fmt(so['big_giveback_pct'], 2, '%')})",
                ]],
            )
        )
    else:
        lines.append("trade scan 생략됨.")

    lines.append("")
    lines.append("### 7) sell_omen 효과 분해 — 수익 vs MDD")
    lines.append("관측 trade 기준 비교이며, 동일 룰북 counterfactual 재백테스트는 아님.")
    if trade_scan:
        groups = trade_scan["groups"]
        comp_names = ["sell_omen_enabled_true", "sell_omen_enabled_false", "exit_sell_omen", "exit_not_sell_omen", "selected_rulebooks", "survivor_tickers", "all"]
        comp_rows = []
        for name in comp_names:
            row = groups.get(name) or {}
            comp_rows.append([
                name,
                row.get("n", 0),
                fmt(row.get("win_rate"), 2, "%"),
                fmt(row.get("avg_pnl_pct"), 4, "%"),
                fmt(row.get("sum_pnl_krw"), 0),
                fmt(row.get("avg_mae_pct"), 4, "%"),
                fmt(row.get("worst_mae_pct"), 4, "%"),
                fmt(row.get("approx_mdd_pct_points"), 4, "pp"),
            ])
        lines.append(table(["group", "n", "WR", "avg pnl", "sum KRW", "avg MAE", "worst MAE", "approx MDD"], comp_rows))
    else:
        lines.append("trade scan 생략됨.")

    lines.append("")
    lines.append("### 8) shift+1 robustness")
    lines.append("sell_omen exit일의 직전 holding row score가 threshold 이상이면 +1일 지연 신호에도 유지되는 것으로 보는 proxy.")
    if trade_scan:
        sh = trade_scan["shift"]
        lines.append(
            table(
                ["sell_omen exits", "prev score available", "prev hit", "hit/available", "hit/all exits", "avg threshold", "avg exit score", "avg prev score"],
                [[
                    sh["sell_omen_exit_count"],
                    sh["prev_score_available"],
                    sh["prev_score_hit_count"],
                    fmt(sh["prev_score_hit_pct_of_available"], 2, "%"),
                    fmt(sh["prev_score_hit_pct_of_sell_omen_exits"], 2, "%"),
                    fmt(sh["avg_threshold"], 4),
                    fmt(sh["avg_exit_score"], 4),
                    fmt(sh["avg_prev_score"], 4),
                ]],
            )
        )
    else:
        lines.append("trade scan 생략됨.")

    lines.append("")
    lines.append("### 9) stop_loss_atr vs take_profit_atr 분포 — 손익비 역전")
    lines.append(
        table(
            ["범위", "n", "take<stop", "take<stop%", "stop median", "take median", "take/stop median", "stop range", "take range"],
            [
                [
                    "selected survivors",
                    selected_ratio["n"],
                    selected_ratio["inverted"],
                    fmt(selected_ratio["inverted_pct"], 2, "%"),
                    fmt(selected_ratio["stop_median"], 4),
                    fmt(selected_ratio["take_median"], 4),
                    fmt(selected_ratio["take_stop_ratio_median"], 4),
                    f"{fmt(selected_ratio['stop_min'], 4)}~{fmt(selected_ratio['stop_max'], 4)}",
                    f"{fmt(selected_ratio['take_min'], 4)}~{fmt(selected_ratio['take_max'], 4)}",
                ],
                [
                    "all topn rulebooks",
                    all_ratio["n"],
                    all_ratio["inverted"],
                    fmt(all_ratio["inverted_pct"], 2, "%"),
                    fmt(all_ratio["stop_median"], 4),
                    fmt(all_ratio["take_median"], 4),
                    fmt(all_ratio["take_stop_ratio_median"], 4),
                    f"{fmt(all_ratio['stop_min'], 4)}~{fmt(all_ratio['stop_max'], 4)}",
                    f"{fmt(all_ratio['take_min'], 4)}~{fmt(all_ratio['take_max'], 4)}",
                ],
            ],
        )
    )

    lines.append("")
    lines.append("## 참고: exit_reason 상위")
    if trade_scan:
        erows = [[k, v] for k, v in trade_scan["exit_reason_counts"].most_common(20)]
        lines.append(table(["exit_reason", "count"], erows))
    else:
        lines.append("trade scan 생략됨.")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LR8D ABCD post-run coverage + analysis report")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="LR8D output directory")
    parser.add_argument("--prefix", default=RUN_PREFIX, help="artifact filename prefix")
    parser.add_argument("--condition-dir", default=str(DEFAULT_CONDITION_DIR), help="condition_db csv directory")
    parser.add_argument("--expected-period-rows", type=int, default=EXPECTED_PERIOD_ROWS)
    parser.add_argument("--skip-coverage", action="store_true", help="skip condition_db coverage scan")
    parser.add_argument("--skip-trades", action="store_true", help="skip large trades.jsonl scan")
    parser.add_argument("--no-write-report", action="store_true", help="print only; do not write markdown report")
    parser.add_argument("--report-out", default="", help="custom report path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir)
    prefix = str(args.prefix)
    topn_path = run_dir / f"{prefix}_topn.jsonl"
    rulebooks_path = run_dir / f"{prefix}_topn_rulebooks.jsonl"
    trades_path = run_dir / f"{prefix}_trades.jsonl"

    if not run_dir.exists():
        raise SystemExit(f"RUN 디렉터리가 없습니다: {run_dir}")
    topn_rows = read_jsonl(topn_path)
    rulebook_rows = read_jsonl(rulebooks_path)
    survivors, survivor_source = load_survivors(run_dir, prefix, topn_rows)

    by_hash, by_ticker_year_hash = build_rulebook_maps(rulebook_rows)
    selected_rulebooks: list[dict[str, Any]] = []
    missing_selected_rulebooks = 0
    for survivor in survivors:
        rb_row = pick_rulebook_for_survivor(survivor, by_hash, by_ticker_year_hash)
        rb = rulebook_payload(rb_row)
        if rb:
            selected_rulebooks.append(rb)
        else:
            missing_selected_rulebooks += 1
    all_rulebooks = [rulebook_payload(row) for row in rulebook_rows if rulebook_payload(row)]

    selected_hashes = {selected_hash(row) for row in survivors if selected_hash(row)}
    survivor_tickers = {str(row.get("ticker") or "") for row in survivors if row.get("ticker")}

    trade_scan = None
    if not args.skip_trades:
        trade_scan = scan_trades(trades_path, selected_hashes, survivor_tickers)
    coverage = None
    if not args.skip_coverage:
        coverage = analyze_condition_coverage(Path(args.condition_dir))

    integrity = analyze_integrity(
        run_dir=run_dir,
        prefix=prefix,
        topn_rows=topn_rows,
        rulebook_rows=rulebook_rows,
        trade_scan=trade_scan,
        expected_period_rows=int(args.expected_period_rows),
    )
    if missing_selected_rulebooks:
        integrity["missing_selected_rulebooks"] = missing_selected_rulebooks

    report = render_report(
        {
            "integrity": integrity,
            "coverage": coverage,
            "survivors": survivors,
            "survivor_source": survivor_source,
            "selected_rulebooks": selected_rulebooks,
            "all_rulebooks": all_rulebooks,
            "trade_scan": trade_scan,
            "oos_2024_2026": oos_2024_2026_summary(topn_rows, survivor_tickers),
        }
    )
    print(report)

    if not args.no_write_report:
        report_out = Path(args.report_out) if args.report_out else run_dir / f"{prefix}_postrun_analysis.md"
        report_out.write_text(report + "\n", encoding="utf-8")
        print(f"\n[report_written] {report_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
