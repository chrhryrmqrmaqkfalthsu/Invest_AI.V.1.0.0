"""
Central Portfolio — v0 comparison_infra_gate.

목적: 진짜 중앙 시뮬레이터 검증이 아니다.
  데이터 결정성 + trade 정규화 + row-level comparator + 결과 저장 구조를 검증한다.
  reference/candidate 모두 run_backtest()를 "같은 df 객체"로 호출하는 self-vs-self.
  → 반드시 0 mismatch로 통과해야 정상. mismatch가 나오면 데이터 비결정성 또는 comparator 버그.
참조: docs/CENTRAL_PORTFOLIO_BACKTEST_DESIGN.md §4a (engine_noop 게이트의 선행 인프라)
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from engine.core.data_loader import load_ohlcv
from engine.core.indicators import calc_indicators
from engine.learning.backtest import run_backtest
from engine.strategies.rulebook import Rulebook

MANIFEST_PATH = Path("data/_system/live_universe_lr8d_stage1_manifest.json")
PARAMS_PATH_TMPL = "data/symbols/{ticker}/parameters.json"
OUT_DIR = Path("data/_system/research/central_portfolio/noop_gate")

STRING_FIELDS = ["ticker", "entry_date", "exit_date", "exit_reason"]
INT_FIELDS = ["trade_index", "entry_shares", "total_shares", "holding_days"]
FLOAT_FIELDS = [
    "entry_price",
    "exit_price",
    "fill_price_base",
    "trigger_price",
    "pnl_krw",
    "pnl_pct",
    "commission",
    "avg_cost",
]
ALL_FIELDS = STRING_FIELDS + INT_FIELDS + FLOAT_FIELDS
FLOAT_ABS_TOL = 1e-6


def load_promoted_rulebooks(manifest_path: Path = MANIFEST_PATH) -> list[tuple[str, Rulebook]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tickers = manifest["tickers"]
    out: list[tuple[str, Rulebook]] = []
    for tk in tickers:
        payload = json.loads(Path(PARAMS_PATH_TMPL.format(ticker=tk)).read_text(encoding="utf-8"))
        rb = Rulebook.from_dict(payload["rulebook"])
        out.append((tk, rb))
    return out


def load_fixed_history(ticker: str, years: int, history_end_date: str) -> Any:
    """종목별 1회 로드. reference/candidate가 동일 객체를 공유하도록 호출측에서 캐싱."""
    df = load_ohlcv(
        ticker,
        years=years,
        end_date=history_end_date,
        use_cache=True,
        max_retries=1,
    ).sort_index()
    df = calc_indicators(df).sort_index()
    return df


def _trade_get(trade: Any, key: str, default=None):
    """dict / dataclass 모두 지원."""
    if isinstance(trade, dict):
        return trade.get(key, default)
    if hasattr(trade, "__dataclass_fields__"):
        return getattr(trade, key, default)
    return getattr(trade, key, default)


def normalize_trade_row(ticker: str, idx: int, trade: Any) -> dict[str, Any]:
    if hasattr(trade, "__dataclass_fields__") and not isinstance(trade, dict):
        trade = asdict(trade)
    row: dict[str, Any] = {"ticker": ticker, "trade_index": idx}
    for f in STRING_FIELDS:
        if f == "ticker":
            continue
        v = _trade_get(trade, f)
        row[f] = "" if v is None else str(v)
    for f in INT_FIELDS:
        if f == "trade_index":
            continue
        v = _trade_get(trade, f)
        row[f] = None if v is None else int(v)
    for f in FLOAT_FIELDS:
        v = _trade_get(trade, f)
        row[f] = None if v is None else float(v)
    return row


def run_reference_backtest(
    rb: Rulebook,
    df: Any,
    start_date: str,
    end_date: str,
    position_limit_krw: float,
    commission_rate: float,
    warmup: int,
) -> list[dict[str, Any]]:
    result = run_backtest(
        rb,
        df,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sector_name=(getattr(rb, "sector_name", "tech") or "tech"),
        fitness_mode="legacy",
    )
    return list(result.trades)


def compare_trade_rows(ref_rows: list[dict], cand_rows: list[dict]) -> list[dict[str, Any]]:
    """ticker+trade_index 키로 매칭. 불일치 목록 반환."""
    mismatches: list[dict[str, Any]] = []

    def key(r):
        return (r["ticker"], r["trade_index"])

    ref_map = {key(r): r for r in ref_rows}
    cand_map = {key(r): r for r in cand_rows}
    all_keys = sorted(set(ref_map) | set(cand_map))

    for k in all_keys:
        r = ref_map.get(k)
        c = cand_map.get(k)
        if r is None or c is None:
            mismatches.append(
                {
                    "ticker": k[0],
                    "trade_index": k[1],
                    "field": "_row_presence",
                    "ref": "missing" if r is None else "present",
                    "candidate": "missing" if c is None else "present",
                    "diff": "row count mismatch",
                }
            )
            continue
        for f in ALL_FIELDS:
            rv, cv = r.get(f), c.get(f)
            if f in FLOAT_FIELDS:
                if rv is None and cv is None:
                    continue
                if rv is None or cv is None:
                    ok = False
                    diff = "one-side None"
                else:
                    diff = abs(float(rv) - float(cv))
                    ok = (not math.isnan(diff)) and diff <= FLOAT_ABS_TOL
                if not ok:
                    mismatches.append(
                        {
                            "ticker": k[0],
                            "trade_index": k[1],
                            "field": f,
                            "ref": rv,
                            "candidate": cv,
                            "diff": diff,
                        }
                    )
            else:
                if rv != cv:
                    mismatches.append(
                        {
                            "ticker": k[0],
                            "trade_index": k[1],
                            "field": f,
                            "ref": rv,
                            "candidate": cv,
                            "diff": "neq",
                        }
                    )
    return mismatches


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "reference_trades.csv", ref_rows)
    _write_csv(out_dir / "candidate_trades.csv", cand_rows)
    _write_csv(out_dir / "mismatches.csv", mismatches)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")


def run_comparison_infra_gate(
    start_date: str,
    end_date: str,
    history_end_date: str,
    position_limit_krw: float = 30.0,
    commission_rate: float = 0.0005,
    warmup: int = 200,
    years: int = 3,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    rbs = load_promoted_rulebooks()
    ref_rows: list[dict] = []
    cand_rows: list[dict] = []

    for ticker, rb in rbs:
        df = load_fixed_history(ticker, years=years, history_end_date=history_end_date)
        # reference / candidate 모두 동일한 df 객체 사용 (v0 self-vs-self)
        ref_trades = run_reference_backtest(
            rb,
            df,
            start_date,
            end_date,
            position_limit_krw,
            commission_rate,
            warmup,
        )
        cand_trades = run_reference_backtest(
            rb,
            df,
            start_date,
            end_date,
            position_limit_krw,
            commission_rate,
            warmup,
        )
        for i, trade in enumerate(ref_trades):
            ref_rows.append(normalize_trade_row(ticker, i, trade))
        for i, trade in enumerate(cand_trades):
            cand_rows.append(normalize_trade_row(ticker, i, trade))

    mismatches = compare_trade_rows(ref_rows, cand_rows)
    summary = {
        "gate": "comparison_infra_gate_v0",
        "self_vs_self": True,
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "tickers": [tk for tk, _ in rbs],
        "ref_trade_count": len(ref_rows),
        "candidate_trade_count": len(cand_rows),
        "mismatch_count": len(mismatches),
        "passed": len(mismatches) == 0 and len(ref_rows) == len(cand_rows),
    }
    write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir)
    return summary
