#!/usr/bin/env python3
"""Preflight sell-omen score coverage before expensive research runs.

This script is both a RUN gate and a before/after measurement tool for
sell_omen score table regeneration. It verifies that the score table covers the
intended ticker universe and, when trades are supplied, the observed
(ticker,date) pairs used by the exit simulator.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_RUN_DIR = Path("data/_system/research/lr8d_abcd_20260608")
DEFAULT_SCORE_TABLE = Path("data/_system/ml_sell_omen/sell_omen_scores.csv")
DEFAULT_TARGET_TICKERS = DEFAULT_RUN_DIR / "lr8d_target_tickers.txt"
DEFAULT_SURVIVORS = DEFAULT_RUN_DIR / "lr8d_abcd_survivors.jsonl"
DEFAULT_TRADES = DEFAULT_RUN_DIR / "lr8d_abcd_trades.jsonl"
DEFAULT_SYMBOLS_DIR = Path("data/symbols")
DEFAULT_STAGE1_PROMOTION_ID = "lr8d_stage1_20260609"
DEFAULT_YEARS = (2024, 2025, 2026)
REQUIRED_SCORE_COLUMNS = {"ticker", "Date", "sell_omen_score"}


@dataclass(frozen=True)
class CoverageResult:
    name: str
    total: int
    covered: int
    missing: int
    coverage: float
    missing_sample: list[str]


@dataclass(frozen=True)
class TradeDateCoverage:
    observations: int
    matched_observations: int
    coverage: float
    unique_pairs: int
    matched_unique_pairs: int
    unique_coverage: float
    date_source: str
    years: list[int]


@dataclass(frozen=True)
class ScoreSummary:
    rows: int
    tickers: int
    date_min: str
    date_max: str
    score_min: float | None
    score_median: float | None
    score_p90: float | None
    score_p99: float | None
    score_max: float | None
    ge_05: int
    ge_07: int
    ge_08: int


def _norm_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _date_key(value: Any) -> str:
    if value is None:
        return ""
    try:
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return ""
        return str(ts.date())
    except Exception:
        text = str(value or "").strip()
        return text[:10] if len(text) >= 10 else ""


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except Exception as exc:
                raise ValueError(f"invalid JSONL {path}:{line_no}: {exc}") from exc
            if isinstance(row, dict):
                yield row


def load_score_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"score table missing: {path}")
    df = pd.read_csv(path)
    missing = REQUIRED_SCORE_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"score table required columns missing: {sorted(missing)} in {path}")
    out = df.copy()
    out["ticker"] = out["ticker"].map(_norm_ticker)
    out["Date"] = out["Date"].map(_date_key)
    out["sell_omen_score"] = pd.to_numeric(out["sell_omen_score"], errors="coerce")
    out = out.dropna(subset=["sell_omen_score"])
    out = out[(out["ticker"] != "") & (out["Date"] != "")]
    out = out[(out["sell_omen_score"] >= 0.0) & (out["sell_omen_score"] <= 1.0)]
    return out.drop_duplicates(["ticker", "Date"], keep="last").reset_index(drop=True)


def summarize_scores(scores: pd.DataFrame) -> ScoreSummary:
    if scores.empty:
        return ScoreSummary(0, 0, "", "", None, None, None, None, None, 0, 0, 0)
    s = scores["sell_omen_score"].dropna()
    dates = pd.to_datetime(scores["Date"], errors="coerce").dropna()
    return ScoreSummary(
        rows=int(len(scores)),
        tickers=int(scores["ticker"].nunique()),
        date_min=str(dates.min().date()) if len(dates) else "",
        date_max=str(dates.max().date()) if len(dates) else "",
        score_min=float(s.min()) if len(s) else None,
        score_median=float(s.median()) if len(s) else None,
        score_p90=float(s.quantile(0.90)) if len(s) else None,
        score_p99=float(s.quantile(0.99)) if len(s) else None,
        score_max=float(s.max()) if len(s) else None,
        ge_05=int((s >= 0.5).sum()),
        ge_07=int((s >= 0.7).sum()),
        ge_08=int((s >= 0.8).sum()),
    )


def load_ticker_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {_norm_ticker(line) for line in path.read_text(encoding="utf-8").splitlines() if _norm_ticker(line)}


def load_survivor_tickers(path: Path, combo_id: str | None = None) -> set[str]:
    if not path.exists():
        return set()
    tickers: set[str] = set()
    for row in _read_jsonl(path):
        if combo_id is not None and row.get("combo_id") != combo_id:
            continue
        t = _norm_ticker(row.get("ticker"))
        if t:
            tickers.add(t)
    return tickers


def load_promoted_tickers(symbols_dir: Path, promotion_id: str) -> set[str]:
    if not symbols_dir.exists():
        return set()
    tickers: set[str] = set()
    for directory in sorted(p for p in symbols_dir.iterdir() if p.is_dir()):
        path = directory / "parameters.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        promotion = payload.get("promotion") if isinstance(payload, dict) else None
        if isinstance(promotion, dict) and str(promotion.get("promotion_id") or "").strip() == promotion_id:
            tickers.add(_norm_ticker(directory.name))
    return tickers


def coverage_for_tickers(name: str, tickers: set[str], score_tickers: set[str], sample: int = 20) -> CoverageResult:
    clean = {t for t in tickers if t}
    covered = clean & score_tickers
    missing = sorted(clean - score_tickers)
    coverage = float(len(covered) / len(clean)) if clean else 1.0
    return CoverageResult(
        name=name,
        total=len(clean),
        covered=len(covered),
        missing=len(missing),
        coverage=coverage,
        missing_sample=missing[:sample],
    )


def _trade_dates_from_holding_path(trade: dict[str, Any]) -> list[str]:
    dates: list[str] = []
    for row in trade.get("holding_path_full") or []:
        if isinstance(row, dict):
            d = _date_key(row.get("date"))
            if d:
                dates.append(d)
    if dates:
        return dates
    d = _date_key(trade.get("exit_snapshot_date") or trade.get("exit_date") or trade.get("entry_date"))
    return [d] if d else []


def collect_trade_date_coverage(
    trades_path: Path,
    score_keys: set[tuple[str, str]],
    *,
    years: set[int],
    date_source: str = "holding_path",
) -> TradeDateCoverage:
    if not trades_path.exists():
        return TradeDateCoverage(0, 0, 1.0, 0, 0, 1.0, date_source, sorted(years))

    observations = 0
    matched = 0
    unique_pairs: set[tuple[str, str]] = set()
    matched_unique: set[tuple[str, str]] = set()

    for row in _read_jsonl(trades_path):
        ticker = _norm_ticker(row.get("ticker"))
        for trade in row.get("trades") or []:
            if not isinstance(trade, dict):
                continue
            t = _norm_ticker(trade.get("ticker") or ticker)
            if not t:
                continue
            if date_source == "holding_path":
                dates = _trade_dates_from_holding_path(trade)
            elif date_source == "entry_date":
                d = _date_key(trade.get("entry_date"))
                dates = [d] if d else []
            elif date_source == "exit_date":
                d = _date_key(trade.get("exit_date"))
                dates = [d] if d else []
            elif date_source == "exit_snapshot_date":
                d = _date_key(trade.get("exit_snapshot_date") or trade.get("exit_date"))
                dates = [d] if d else []
            else:
                raise ValueError(f"unsupported date_source: {date_source}")

            for d in dates:
                if not d:
                    continue
                try:
                    year = int(d[:4])
                except Exception:
                    continue
                if year not in years:
                    continue
                pair = (t, d)
                observations += 1
                unique_pairs.add(pair)
                if pair in score_keys:
                    matched += 1
                    matched_unique.add(pair)

    coverage = float(matched / observations) if observations else 1.0
    unique_coverage = float(len(matched_unique) / len(unique_pairs)) if unique_pairs else 1.0
    return TradeDateCoverage(
        observations=observations,
        matched_observations=matched,
        coverage=coverage,
        unique_pairs=len(unique_pairs),
        matched_unique_pairs=len(matched_unique),
        unique_coverage=unique_coverage,
        date_source=date_source,
        years=sorted(years),
    )


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], list[str], list[str]]:
    scores = load_score_table(Path(args.score_table))
    score_tickers = set(scores["ticker"].astype(str))
    score_keys = set(zip(scores["ticker"].astype(str), scores["Date"].astype(str)))

    target = load_ticker_file(Path(args.target_tickers)) if args.target_tickers else set()
    stage1 = load_promoted_tickers(Path(args.symbols_dir), args.stage1_promotion_id)
    survivors = load_survivor_tickers(Path(args.survivors)) if args.survivors else set()
    strict = load_survivor_tickers(Path(args.survivors), combo_id="strict_k3") if args.survivors else set()

    coverages = [
        coverage_for_tickers("target", target, score_tickers),
        coverage_for_tickers("stage1", stage1, score_tickers),
        coverage_for_tickers("survivors", survivors, score_tickers),
        coverage_for_tickers("strict_k3", strict, score_tickers),
    ]
    years = {int(y) for y in str(args.years).replace(",", " ").split() if str(y).strip()}
    trade_cov = collect_trade_date_coverage(
        Path(args.trades),
        score_keys,
        years=years,
        date_source=args.trade_date_source,
    )
    score_summary = summarize_scores(scores)

    failures: list[str] = []
    warnings: list[str] = []
    thresholds = {
        "target": args.min_target_ticker_coverage,
        "stage1": args.min_stage1_ticker_coverage,
        "survivors": args.min_survivor_ticker_coverage,
        "strict_k3": args.min_strict_k3_ticker_coverage,
    }
    for cov in coverages:
        threshold = float(thresholds[cov.name])
        if cov.total > 0 and cov.coverage < threshold:
            failures.append(f"{cov.name} ticker coverage {cov.coverage:.2%} < {threshold:.2%}")

    trade_threshold = float(args.min_trade_date_coverage)
    if trade_cov.coverage < trade_threshold:
        msg = f"trade ticker_date coverage {trade_cov.coverage:.2%} < {trade_threshold:.2%}"
        if args.trade_date_gate == "fail":
            failures.append(msg)
        elif args.trade_date_gate == "warn":
            warnings.append(msg)

    if score_summary.score_p99 is not None and score_summary.score_p99 < float(args.score_p99_warning_floor):
        warnings.append(
            f"score p99 {score_summary.score_p99:.4f} < warning floor {float(args.score_p99_warning_floor):.4f}"
        )

    report = {
        "score_table": str(args.score_table),
        "score_summary": asdict(score_summary),
        "coverage": [asdict(c) for c in coverages],
        "trade_date_coverage": asdict(trade_cov),
        "thresholds": {
            "min_target_ticker_coverage": args.min_target_ticker_coverage,
            "min_stage1_ticker_coverage": args.min_stage1_ticker_coverage,
            "min_survivor_ticker_coverage": args.min_survivor_ticker_coverage,
            "min_strict_k3_ticker_coverage": args.min_strict_k3_ticker_coverage,
            "min_trade_date_coverage": args.min_trade_date_coverage,
            "trade_date_gate": args.trade_date_gate,
            "score_p99_warning_floor": args.score_p99_warning_floor,
        },
        "failures": failures,
        "warnings": warnings,
        "ok": not failures,
    }
    return report, failures, warnings


def _ticker_threshold_for_name(report: dict[str, Any], name: str) -> float:
    key_by_name = {
        "target": "min_target_ticker_coverage",
        "stage1": "min_stage1_ticker_coverage",
        "survivors": "min_survivor_ticker_coverage",
        "strict_k3": "min_strict_k3_ticker_coverage",
    }
    return float(report["thresholds"].get(key_by_name.get(name, ""), 0.0))


def print_human(report: dict[str, Any]) -> None:
    ss = report["score_summary"]
    print("=== sell_omen coverage preflight ===")
    print(f"score_table: {report['score_table']}")
    print(
        "score rows={rows} tickers={tickers} dates={date_min}..{date_max} "
        "median={score_median} p90={score_p90} p99={score_p99} max={score_max}".format(**ss)
    )
    print(f"score >=0.5/{ss['ge_05']} >=0.7/{ss['ge_07']} >=0.8/{ss['ge_08']}")
    print("\n[ticker coverage]")
    for cov in report["coverage"]:
        threshold = _ticker_threshold_for_name(report, cov["name"])
        status_ok = True if cov["total"] == 0 else cov["coverage"] >= threshold
        print(
            f"{_status(status_ok)} "
            f"{cov['name']}: {cov['covered']}/{cov['total']} = {cov['coverage']:.2%}; "
            f"missing_sample={cov['missing_sample']}"
        )
    tc = report["trade_date_coverage"]
    print("\n[trade ticker_date coverage]")
    print(
        f"date_source={tc['date_source']} years={tc['years']} "
        f"observations={tc['matched_observations']}/{tc['observations']}={tc['coverage']:.2%} "
        f"unique={tc['matched_unique_pairs']}/{tc['unique_pairs']}={tc['unique_coverage']:.2%}"
    )
    if report["warnings"]:
        print("\n[warnings]")
        for msg in report["warnings"]:
            print(f"WARN {msg}")
    if report["failures"]:
        print("\n[failures]")
        for msg in report["failures"]:
            print(f"FAIL {msg}")
    print(f"\nresult: {'PASS' if report['ok'] else 'FAIL'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="sell_omen score coverage preflight")
    parser.add_argument("--score-table", type=Path, default=DEFAULT_SCORE_TABLE)
    parser.add_argument("--target-tickers", type=Path, default=DEFAULT_TARGET_TICKERS)
    parser.add_argument("--survivors", type=Path, default=DEFAULT_SURVIVORS)
    parser.add_argument("--trades", type=Path, default=DEFAULT_TRADES)
    parser.add_argument("--symbols-dir", type=Path, default=DEFAULT_SYMBOLS_DIR)
    parser.add_argument("--stage1-promotion-id", default=DEFAULT_STAGE1_PROMOTION_ID)
    parser.add_argument("--years", default=" ".join(str(y) for y in DEFAULT_YEARS))
    parser.add_argument(
        "--trade-date-source",
        choices=["holding_path", "entry_date", "exit_date", "exit_snapshot_date"],
        default="holding_path",
    )
    parser.add_argument("--min-target-ticker-coverage", type=float, default=0.95)
    parser.add_argument("--min-stage1-ticker-coverage", type=float, default=0.95)
    parser.add_argument("--min-survivor-ticker-coverage", type=float, default=0.95)
    parser.add_argument("--min-strict-k3-ticker-coverage", type=float, default=0.95)
    parser.add_argument("--min-trade-date-coverage", type=float, default=0.85)
    parser.add_argument("--trade-date-gate", choices=["off", "warn", "fail"], default="warn")
    parser.add_argument("--score-p99-warning-floor", type=float, default=0.30)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report, failures, _warnings = build_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(report)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
