#!/usr/bin/env python3
"""Build a filtered Stage3 live-pool repository.

The raw Stage3 batch directory is append-only research output.  Live should not
scan that whole tree on every startup, and it should not ingest every exit-gene
combination blindly.  This script performs the first-pass filtering step and
writes a compact repository that live central-control can mix with the existing
Stage2-B pool.

Default policy is intentionally conservative:

* only rows that passed Stage3 basic eligibility are kept;
* only rank-1 row per ticker is kept;
* pure-OOS periods must meet minimum expectancy/trade/drawdown guards.

The output JSONL preserves the original Stage3 catalog row shape so it can be
loaded by ``engine.central.entity_loader.load_entities_from_catalog`` without a
second adapter.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BATCH_ROOT = Path("exp_batch_stage123_2009_20260616_full")
DEFAULT_OUT_DIR = Path("data/_system/central/stage3_live_pool")
DEFAULT_OUTPUT_NAME = "stage3_live_pool.jsonl"
DEFAULT_REJECTED_SAMPLE_NAME = "rejected_sample.jsonl"
DEFAULT_SUMMARY_NAME = "summary.json"


@dataclass(frozen=True)
class FilterConfig:
    batch_root: Path = DEFAULT_BATCH_ROOT
    out_dir: Path = DEFAULT_OUT_DIR
    output_name: str = DEFAULT_OUTPUT_NAME
    rejected_sample_name: str = DEFAULT_REJECTED_SAMPLE_NAME
    summary_name: str = DEFAULT_SUMMARY_NAME
    require_eligible: bool = True
    max_rank_per_ticker: int = 1
    top_per_ticker: int = 1
    max_rows: int = 0
    min_pure_oos_periods: int = 3
    min_expectancy_pct: float = 1.0
    min_trade_count: int = 3
    max_drawdown_floor_pct: float = -50.0
    min_profit_factor: float = 1.0
    rejected_sample_limit: int = 200
    dry_run: bool = False


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    batch_root: str
    out_dir: str
    output_path: str
    summary_path: str
    rejected_sample_path: str
    source_catalog_files: int
    source_rows: int
    kept_rows: int
    kept_tickers: int
    rejected_rows: int
    reject_reasons: dict[str, int]
    top_per_ticker: int
    max_rank_per_ticker: int
    dry_run: bool


@dataclass(frozen=True)
class CandidateRow:
    row: dict[str, Any]
    source_path: str
    source_line: int
    ticker: str
    rank: int
    avg_expectancy_pct: float
    avg_profit_factor: float
    min_trade_count: float
    worst_drawdown_pct: float


def iter_stage3_catalog_rows(batch_root: Path) -> Iterable[CandidateRow]:
    ticker_root = Path(batch_root) / "tickers"
    if not ticker_root.exists():
        raise FileNotFoundError(f"Stage3 ticker root not found: {ticker_root}")
    for catalog_path in sorted(ticker_root.glob("*/stage3/stage3_profile_catalog.jsonl")):
        with catalog_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                ticker = _ticker(row)
                metrics = _pure_oos_metric_rows(row)
                rank = _safe_int(row.get("rank"), default=10**9)
                yield CandidateRow(
                    row=dict(row),
                    source_path=str(catalog_path),
                    source_line=line_no,
                    ticker=ticker,
                    rank=rank,
                    avg_expectancy_pct=_avg(_safe_float(m.get("expectancy_pct")) for m in metrics),
                    avg_profit_factor=_avg(_safe_float(m.get("profit_factor")) for m in metrics),
                    min_trade_count=min((_safe_float(m.get("trade_count")) for m in metrics), default=0.0),
                    worst_drawdown_pct=min((_safe_float(m.get("max_drawdown_pct")) for m in metrics), default=0.0),
                )


def build_stage3_live_pool(config: FilterConfig) -> BuildResult:
    rows = list(iter_stage3_catalog_rows(config.batch_root))
    rejected: list[dict[str, Any]] = []
    reject_counts: Counter[str] = Counter()
    passed: list[CandidateRow] = []

    for item in rows:
        ok, reasons = row_passes_first_filter(item.row, config)
        if ok:
            passed.append(item)
        else:
            for reason in reasons:
                reject_counts[reason] += 1
            if len(rejected) < max(0, int(config.rejected_sample_limit)):
                rejected.append(
                    {
                        "ticker": item.ticker,
                        "rank": item.rank,
                        "rulebook_hash": item.row.get("rulebook_hash"),
                        "source_path": item.source_path,
                        "source_line": item.source_line,
                        "reasons": reasons,
                    }
                )

    selected = select_top_rows(passed, config)
    output_path = config.out_dir / config.output_name
    summary_path = config.out_dir / config.summary_name
    rejected_path = config.out_dir / config.rejected_sample_name

    result = BuildResult(
        ok=True,
        batch_root=str(config.batch_root),
        out_dir=str(config.out_dir),
        output_path=str(output_path),
        summary_path=str(summary_path),
        rejected_sample_path=str(rejected_path),
        source_catalog_files=len({item.source_path for item in rows}),
        source_rows=len(rows),
        kept_rows=len(selected),
        kept_tickers=len({item.ticker for item in selected}),
        rejected_rows=len(rows) - len(passed),
        reject_reasons=dict(sorted(reject_counts.items())),
        top_per_ticker=int(config.top_per_ticker),
        max_rank_per_ticker=int(config.max_rank_per_ticker),
        dry_run=bool(config.dry_run),
    )

    if not config.dry_run:
        config.out_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(output_path, "".join(_serialize_live_pool_row(item, config) for item in selected))
        _atomic_write_text(rejected_path, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rejected))
        _atomic_write_text(summary_path, json.dumps({**asdict(result), "created_at": _utc_now()}, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return result


def row_passes_first_filter(row: Mapping[str, Any], config: FilterConfig) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ticker = _ticker(row)
    if not ticker:
        reasons.append("missing_ticker")
    if config.require_eligible and row.get("eligible_stage3_basic") is not True:
        reasons.append("not_stage3_basic_eligible")
    rank = _safe_int(row.get("rank"), default=10**9)
    if int(config.max_rank_per_ticker or 0) > 0 and rank > int(config.max_rank_per_ticker):
        reasons.append("rank_above_limit")
    metrics = _pure_oos_metric_rows(row)
    if len(metrics) < int(config.min_pure_oos_periods or 0):
        reasons.append("insufficient_pure_oos_periods")
    for metric in metrics:
        label = str(metric.get("label") or "pure_oos")
        expectancy = _safe_float(metric.get("expectancy_pct"))
        trade_count = _safe_float(metric.get("trade_count"))
        max_dd = _safe_float(metric.get("max_drawdown_pct"))
        profit_factor = _safe_float(metric.get("profit_factor"))
        if expectancy < float(config.min_expectancy_pct):
            reasons.append(f"expectancy_below_floor:{label}")
        if trade_count < float(config.min_trade_count):
            reasons.append(f"trade_count_below_floor:{label}")
        if max_dd < float(config.max_drawdown_floor_pct):
            reasons.append(f"drawdown_below_floor:{label}")
        if profit_factor < float(config.min_profit_factor):
            reasons.append(f"profit_factor_below_floor:{label}")
    return not reasons, reasons


def select_top_rows(rows: Sequence[CandidateRow], config: FilterConfig) -> list[CandidateRow]:
    grouped: dict[str, list[CandidateRow]] = defaultdict(list)
    for item in rows:
        if item.ticker:
            grouped[item.ticker].append(item)
    selected: list[CandidateRow] = []
    per_ticker = max(1, int(config.top_per_ticker or 1))
    for ticker in sorted(grouped):
        ranked = sorted(
            grouped[ticker],
            key=lambda item: (
                item.rank,
                -item.avg_expectancy_pct,
                -item.avg_profit_factor,
                str(item.row.get("rulebook_hash") or ""),
            ),
        )
        selected.extend(ranked[:per_ticker])
    selected.sort(key=lambda item: (item.ticker, item.rank, str(item.row.get("rulebook_hash") or "")))
    if int(config.max_rows or 0) > 0:
        selected = selected[: int(config.max_rows)]
    return selected


def _serialize_live_pool_row(item: CandidateRow, config: FilterConfig) -> str:
    row = dict(item.row)
    row["live_pool_filter"] = {
        "avg_expectancy_pct": item.avg_expectancy_pct,
        "avg_profit_factor": item.avg_profit_factor,
        "created_at": _utc_now(),
        "max_drawdown_floor_pct": float(config.max_drawdown_floor_pct),
        "max_rank_per_ticker": int(config.max_rank_per_ticker),
        "min_expectancy_pct": float(config.min_expectancy_pct),
        "min_profit_factor": float(config.min_profit_factor),
        "min_pure_oos_periods": int(config.min_pure_oos_periods),
        "min_trade_count": int(config.min_trade_count),
        "source_line": item.source_line,
        "source_path": item.source_path,
        "top_per_ticker": int(config.top_per_ticker),
        "version": "stage3_live_pool_v1",
    }
    return json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"


def _pure_oos_metric_rows(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    period_results = row.get("period_results")
    out: list[dict[str, Any]] = []
    if isinstance(period_results, Mapping):
        iterator = period_results.items()
    elif isinstance(period_results, list):
        iterator = ((str(i), item) for i, item in enumerate(period_results))
    else:
        iterator = []
    for label, payload in iterator:
        if not isinstance(payload, Mapping):
            continue
        if str(payload.get("role") or "").lower() != "pure_oos":
            continue
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else payload
        metric_row = dict(metrics or {})
        metric_row["label"] = str(payload.get("label") or label)
        out.append(metric_row)
    return out


def _ticker(row: Mapping[str, Any]) -> str:
    rb = row.get("rulebook") if isinstance(row.get("rulebook"), Mapping) else {}
    return str(row.get("ticker") or rb.get("ticker") or "").strip().upper()


def _avg(values: Iterable[float]) -> float:
    vals = [float(v or 0.0) for v in values]
    return sum(vals) / len(vals) if vals else 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return out if out == out and out not in (float("inf"), float("-inf")) else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build filtered Stage3 live-pool repository")
    parser.add_argument("--batch-root", default=str(DEFAULT_BATCH_ROOT))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    parser.add_argument("--max-rank-per-ticker", type=int, default=1)
    parser.add_argument("--top-per-ticker", type=int, default=1)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--min-pure-oos-periods", type=int, default=3)
    parser.add_argument("--min-expectancy-pct", type=float, default=1.0)
    parser.add_argument("--min-trade-count", type=int, default=3)
    parser.add_argument("--max-drawdown-floor-pct", type=float, default=-50.0)
    parser.add_argument("--min-profit-factor", type=float, default=1.0)
    parser.add_argument("--rejected-sample-limit", type=int, default=200)
    parser.add_argument("--allow-ineligible", action="store_true", help="Do not require eligible_stage3_basic=True")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> FilterConfig:
    return FilterConfig(
        batch_root=Path(args.batch_root),
        out_dir=Path(args.out_dir),
        output_name=str(args.output_name),
        require_eligible=not bool(args.allow_ineligible),
        max_rank_per_ticker=int(args.max_rank_per_ticker),
        top_per_ticker=int(args.top_per_ticker),
        max_rows=int(args.max_rows),
        min_pure_oos_periods=int(args.min_pure_oos_periods),
        min_expectancy_pct=float(args.min_expectancy_pct),
        min_trade_count=int(args.min_trade_count),
        max_drawdown_floor_pct=float(args.max_drawdown_floor_pct),
        min_profit_factor=float(args.min_profit_factor),
        rejected_sample_limit=int(args.rejected_sample_limit),
        dry_run=bool(args.dry_run),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = build_stage3_live_pool(config_from_args(args))
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
