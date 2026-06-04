#!/usr/bin/env python3
"""E-0 preliminary sell-structure analysis for completed rolling trades.

This is deliberately a read-only/approximate analysis:
- The completed au_1173 trades predate BB entry-context recording.
- Post-exit counterfactuals use subsequently observed daily closes.
- Entry market state is reconstructed from the latest market-history row strictly
  before entry_date (D-1 approximation), not the exact context saved at entry.

Outputs are written outside the completed run directory under
``data/_system/pipeline/v1/analysis/e0_sell_structure/{run_id}``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "data/_system/pipeline/v1/runs"
ANALYSIS_ROOT = ROOT / "data/_system/pipeline/v1/analysis/e0_sell_structure"
DEFAULT_RUN_ID = "au_1173_20260604"
DEFAULT_HORIZONS = (5, 10, 20)
MARKET_HISTORY_PATH = ROOT / "data/_system/market_history.csv"
MARKET_HISTORY_V2_PATH = ROOT / "data/_system/market_history_v2.csv"
PRELIMINARY_WARNING = (
    "근사/예비 분석: au_1173 거래에는 BB 진입 컨텍스트가 없으므로, "
    "post-exit 가격 경로와 진입일 이전 시장 히스토리를 사후 역조회했다. "
    "정식 결론은 BB 이후 거래 데이터로 E-1 분석이 필요하다."
)


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def percentile(values: Iterable[float], q: float) -> float | None:
    clean = sorted(v for raw in values if (v := safe_float(raw)) is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * q
    lo = int(rank)
    hi = min(lo + 1, len(clean) - 1)
    frac = rank - lo
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


def distribution(values: Iterable[float]) -> dict[str, Any]:
    clean = [v for raw in values if (v := safe_float(raw)) is not None]
    return {
        "count": len(clean),
        "avg": sum(clean) / len(clean) if clean else None,
        "min": percentile(clean, 0.0),
        "p10": percentile(clean, 0.10),
        "p25": percentile(clean, 0.25),
        "p50": percentile(clean, 0.50),
        "median": median(clean) if clean else None,
        "p75": percentile(clean, 0.75),
        "p90": percentile(clean, 0.90),
        "max": percentile(clean, 1.0),
    }


def holding_bucket(days: int) -> str:
    if days <= 2:
        return "0-2"
    if days <= 5:
        return "3-5"
    if days <= 10:
        return "6-10"
    if days <= 20:
        return "11-20"
    return "21+"


def pnl_bucket(pnl_pct: float | None) -> str:
    pnl = safe_float(pnl_pct, 0.0) or 0.0
    if pnl < -5:
        return "<-5"
    if pnl < 0:
        return "-5~0"
    if pnl < 5:
        return "0~5"
    return "5+"


def market_regime(score: float | None) -> str:
    value = safe_float(score)
    if value is None:
        return "missing"
    if value >= 70:
        return "bull"
    if value >= 40:
        return "neutral"
    return "bear"


def normalize_trade(raw: dict[str, Any], ticker: str, year: int | None = None) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "year": year,
        "entry_date": raw.get("entry_date"),
        "exit_date": raw.get("exit_date"),
        "entry_price": safe_float(raw.get("entry_price")),
        "avg_cost": safe_float(raw.get("avg_cost"), safe_float(raw.get("entry_price"))),
        "exit_price": safe_float(raw.get("exit_price"), safe_float(raw.get("fill_price_base"))),
        "exit_reason": str(raw.get("exit_reason") or "unknown"),
        "pnl_pct": safe_float(raw.get("pnl_pct")),
        "stress_pnl_pct": safe_float(raw.get("stress_pnl_pct")),
        "holding_days": safe_int(raw.get("holding_days"), 0),
        "add_buy_count": len(raw.get("add_buys") or []),
    }


def collect_trades(run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_dir = RUNS_ROOT / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"run directory not found: {run_dir}")
    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    rolling_files = sorted(run_dir.glob("*/rolling_validation.json"))
    for path in rolling_files:
        ticker = path.parent.name.upper()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        for period in data.get("periods") or []:
            year = safe_int(period.get("year"), 0) or None
            for raw in period.get("trades") or []:
                if isinstance(raw, dict):
                    trades.append(normalize_trade(raw, ticker=ticker, year=year))
    return trades, {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "rolling_file_count": len(rolling_files),
        "trade_count": len(trades),
        "ticker_count": len({t["ticker"] for t in trades}),
        "skip_count": len(skipped),
        "skipped_files": skipped[:50],
    }


def _extract_close_frames(downloaded: pd.DataFrame, tickers: list[str]) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    if downloaded is None or downloaded.empty:
        return out
    if len(tickers) == 1 and not isinstance(downloaded.columns, pd.MultiIndex):
        ticker = tickers[0]
        if "Close" in downloaded.columns:
            series = pd.to_numeric(downloaded["Close"], errors="coerce").dropna()
            series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
            out[ticker] = series[~series.index.duplicated(keep="last")].sort_index()
        return out
    if not isinstance(downloaded.columns, pd.MultiIndex):
        return out
    level0 = set(str(x) for x in downloaded.columns.get_level_values(0))
    level1 = set(str(x) for x in downloaded.columns.get_level_values(1))
    for ticker in tickers:
        try:
            if ticker in level0 and "Close" in level1:
                series = downloaded[ticker]["Close"]
            elif "Close" in level0 and ticker in level1:
                series = downloaded["Close"][ticker]
            else:
                continue
            series = pd.to_numeric(series, errors="coerce").dropna()
            series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
            out[ticker] = series[~series.index.duplicated(keep="last")].sort_index()
        except Exception:
            continue
    return out


def load_price_histories(
    tickers: Iterable[str],
    start_date: str,
    end_date: str,
    *,
    batch_size: int = 50,
) -> tuple[dict[str, pd.Series], list[dict[str, str]]]:
    """Load close histories in memory only; no repository/data files are written."""
    unique = sorted({str(t).upper() for t in tickers if str(t).strip()})
    histories: dict[str, pd.Series] = {}
    failures: list[dict[str, str]] = []
    for offset in range(0, len(unique), max(1, int(batch_size))):
        chunk = unique[offset : offset + max(1, int(batch_size))]
        try:
            raw = yf.download(
                tickers=chunk,
                start=start_date,
                end=end_date,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=True,
                timeout=30,
            )
            histories.update(_extract_close_frames(raw, chunk))
        except Exception as exc:
            failures.extend({"ticker": ticker, "error": f"batch: {type(exc).__name__}: {exc}"} for ticker in chunk)
            continue
        missing = [ticker for ticker in chunk if ticker not in histories]
        for ticker in missing:
            try:
                raw_one = yf.download(
                    tickers=ticker,
                    start=start_date,
                    end=end_date,
                    auto_adjust=False,
                    progress=False,
                    group_by="column",
                    threads=False,
                    timeout=20,
                )
                histories.update(_extract_close_frames(raw_one, [ticker]))
            except Exception as exc:
                failures.append({"ticker": ticker, "error": f"single: {type(exc).__name__}: {exc}"})
        for ticker in missing:
            if ticker not in histories and not any(item["ticker"] == ticker for item in failures):
                failures.append({"ticker": ticker, "error": "no close data returned"})
    return histories, failures


def load_market_history_read_only(
    base_path: Path = MARKET_HISTORY_PATH,
    v2_path: Path = MARKET_HISTORY_V2_PATH,
) -> tuple[pd.DataFrame, str]:
    """Read market history without calling builders that may refresh/write caches."""
    if not base_path.exists():
        return pd.DataFrame(), "missing"
    base = pd.read_csv(base_path, index_col=0, parse_dates=True)
    base.index = pd.to_datetime(base.index).tz_localize(None).normalize()
    score_source = "score"
    if v2_path.exists():
        try:
            v2 = pd.read_csv(v2_path, parse_dates=["date"]).set_index("date")
            v2.index = pd.to_datetime(v2.index).tz_localize(None).normalize()
            if "event_adjustment" in v2.columns:
                base = base.join(v2[["event_adjustment"]], how="left", rsuffix="_v2")
                base["event_adjustment"] = pd.to_numeric(base["event_adjustment"], errors="coerce").fillna(0.0)
                base["score_with_events"] = (
                    pd.to_numeric(base.get("score"), errors="coerce").fillna(50.0) + base["event_adjustment"]
                ).clip(0, 100)
                score_source = "score_with_events"
        except Exception:
            pass
    return base.sort_index(), score_source


def lookup_market_score_before(history: pd.DataFrame, entry_date: Any, score_column: str = "score") -> float | None:
    """Return latest market score strictly before entry_date (D-1 approximation)."""
    if history is None or history.empty or score_column not in history.columns:
        return None
    try:
        ts = pd.Timestamp(entry_date).tz_localize(None).normalize()
    except Exception:
        return None
    pos = history.index.searchsorted(ts, side="left") - 1
    if pos < 0:
        return None
    return safe_float(history.iloc[pos].get(score_column))


def future_path_metrics(
    close: pd.Series,
    exit_date: Any,
    exit_price: float,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> dict[int, dict[str, Any]]:
    """Counterfactual path after an exit using subsequent trading-day closes."""
    if close is None or len(close) == 0 or safe_float(exit_price) in (None, 0.0):
        return {}
    try:
        ts = pd.Timestamp(exit_date).tz_localize(None).normalize()
    except Exception:
        return {}
    series = pd.to_numeric(close, errors="coerce").dropna().sort_index()
    future = series.loc[series.index > ts]
    if future.empty:
        return {}
    reference = float(exit_price)
    out: dict[int, dict[str, Any]] = {}
    for horizon in sorted({int(h) for h in horizons if int(h) > 0}):
        if len(future) < horizon:
            continue
        path = future.iloc[:horizon]
        close_at_horizon = float(path.iloc[horizon - 1])
        additional = (close_at_horizon / reference - 1.0) * 100.0
        min_return = (float(path.min()) / reference - 1.0) * 100.0
        max_return = (float(path.max()) / reference - 1.0) * 100.0
        direction = "higher" if additional > 1e-12 else "lower" if additional < -1e-12 else "flat"
        out[horizon] = {
            "available": True,
            "close_date": path.index[horizon - 1].strftime("%Y-%m-%d"),
            "close_price": close_at_horizon,
            "additional_return_pct": additional,
            "min_return_pct": min_return,
            "max_return_pct": max_return,
            "direction": direction,
        }
    return out


def enrich_trades(
    trades: list[dict[str, Any]],
    price_histories: dict[str, pd.Series],
    market_history: pd.DataFrame,
    market_score_column: str,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    no_price = Counter()
    no_market = 0
    for trade in trades:
        item = dict(trade)
        close = price_histories.get(item["ticker"])
        paths = future_path_metrics(close, item.get("exit_date"), item.get("exit_price"), horizons) if close is not None else {}
        if not paths:
            no_price[item["ticker"]] += 1
        score = lookup_market_score_before(market_history, item.get("entry_date"), market_score_column)
        if score is None:
            no_market += 1
        item["future_paths"] = paths
        item["entry_market_score_approx"] = score
        item["entry_market_regime_approx"] = market_regime(score)
        item["holding_bucket"] = holding_bucket(item.get("holding_days", 0))
        item["pnl_bucket"] = pnl_bucket(item.get("pnl_pct"))
        enriched.append(item)
    return enriched, {
        "trades_without_future_path": sum(no_price.values()),
        "tickers_without_future_path_sample": [t for t, _ in no_price.most_common(30)],
        "trades_without_market_score": no_market,
    }


def summarize_counterfactual(trades: list[dict[str, Any]], horizons: Iterable[int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for horizon in sorted({int(h) for h in horizons}):
        rows = [t["future_paths"][horizon] for t in trades if horizon in (t.get("future_paths") or {})]
        directions = Counter(row["direction"] for row in rows)
        n = len(rows)
        out[str(horizon)] = {
            "observed_count": n,
            "lower_count": directions.get("lower", 0),
            "lower_rate_pct": directions.get("lower", 0) / n * 100.0 if n else None,
            "higher_count": directions.get("higher", 0),
            "higher_rate_pct": directions.get("higher", 0) / n * 100.0 if n else None,
            "flat_count": directions.get("flat", 0),
            "flat_rate_pct": directions.get("flat", 0) / n * 100.0 if n else None,
            "additional_return_pct": distribution(row["additional_return_pct"] for row in rows),
            "min_return_pct": distribution(row["min_return_pct"] for row in rows),
            "max_return_pct": distribution(row["max_return_pct"] for row in rows),
        }
    return out


def grouped_counterfactual(
    trades: list[dict[str, Any]],
    group_key: str,
    horizons: Iterable[int],
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get(group_key) or "unknown")].append(trade)
    return {
        key: {"trade_count": len(rows), "horizons": summarize_counterfactual(rows, horizons)}
        for key, rows in sorted(groups.items())
    }


def summarize_market_regimes(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_reason: dict[str, dict[str, Any]] = {}
    for reason in sorted({str(t.get("exit_reason")) for t in trades}):
        rows = [t for t in trades if t.get("exit_reason") == reason]
        regimes = Counter(t.get("entry_market_regime_approx") or "missing" for t in rows)
        by_reason[reason] = {
            "count": len(rows),
            "regime_counts": dict(regimes),
            "regime_ratios_pct": {k: v / len(rows) * 100.0 if rows else 0.0 for k, v in regimes.items()},
            "pnl_by_regime": {
                regime: distribution(t.get("pnl_pct") for t in rows if t.get("entry_market_regime_approx") == regime)
                for regime in ("bull", "neutral", "bear", "missing")
            },
        }
    stop = by_reason.get("stop_loss", {"count": 0, "regime_counts": {}, "regime_ratios_pct": {}})
    return {
        "method": "entry_date 이전의 최신 market_history 행(D-1 근사), score>=70 bull / 40~70 neutral / <40 bear",
        "by_exit_reason": by_reason,
        "stop_loss_focus": stop,
    }


def analyze_sell_structure(
    trades: list[dict[str, Any]],
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    horizons = tuple(sorted({int(h) for h in horizons if int(h) > 0}))
    trailing = [t for t in trades if t.get("exit_reason") == "trailing"]
    timeout = [t for t in trades if t.get("exit_reason") == "time_out"]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "analysis_label": "E-0 근사/예비 매도 구조 분석",
        "warning": PRELIMINARY_WARNING,
        "meta": dict(meta or {}),
        "trade_count": len(trades),
        "exit_reason_counts": dict(Counter(t.get("exit_reason") for t in trades)),
        "trailing_counterfactual": {
            "interpretation": "청산 후 N거래일 종가가 더 낮으면 방어 효과 근사, 더 높으면 기회 손실 근사",
            "trade_count": len(trailing),
            "exit_pnl_pct": distribution(t.get("pnl_pct") for t in trailing),
            "horizons": summarize_counterfactual(trailing, horizons),
            "by_holding_bucket": grouped_counterfactual(trailing, "holding_bucket", horizons),
            "by_exit_pnl_bucket": grouped_counterfactual(trailing, "pnl_bucket", horizons),
        },
        "post_timeout": {
            "interpretation": "time_out 시 매도하지 않고 N거래일 더 보유했다는 단순 사후 가정의 추가 수익률",
            "trade_count": len(timeout),
            "exit_pnl_pct": distribution(t.get("pnl_pct") for t in timeout),
            "horizons": summarize_counterfactual(timeout, horizons),
        },
        "entry_market_regime_approx": summarize_market_regimes(trades),
    }


def fmt(value: Any, digits: int = 3) -> str:
    val = safe_float(value)
    return "" if val is None else f"{val:.{digits}f}"


def render_text(analysis: dict[str, Any]) -> str:
    meta = analysis.get("meta", {}) or {}
    lines = [
        "=" * 118,
        "E-0 Preliminary Sell-Structure Analysis (근사/예비)",
        "=" * 118,
        f"WARNING: {analysis.get('warning')}",
        f"generated_at: {analysis.get('generated_at')}",
        f"run_id: {meta.get('run_id')}",
        f"trade_count: {analysis.get('trade_count')}",
        f"price_history_loaded: {meta.get('price_history_loaded_count')} / {meta.get('price_history_requested_count')}",
        f"trades_without_future_path: {meta.get('trades_without_future_path')}",
        f"trades_without_market_score: {meta.get('trades_without_market_score')}",
        "",
        "[1] Trailing counterfactual — lower=방어 효과 근사 / higher=기회 손실 근사",
        "horizon | observed | lower% | higher% | avg_additional% | p50_additional% | avg_min% | avg_max%",
        "-" * 100,
    ]
    for horizon, row in (analysis.get("trailing_counterfactual", {}).get("horizons", {}) or {}).items():
        add = row.get("additional_return_pct", {}) or {}
        mn = row.get("min_return_pct", {}) or {}
        mx = row.get("max_return_pct", {}) or {}
        lines.append(
            f"{horizon:>7s} | {row.get('observed_count', 0):8d} | {fmt(row.get('lower_rate_pct'), 2):>6s} | "
            f"{fmt(row.get('higher_rate_pct'), 2):>7s} | {fmt(add.get('avg')):>15s} | {fmt(add.get('p50')):>15s} | "
            f"{fmt(mn.get('avg')):>8s} | {fmt(mx.get('avg')):>8s}"
        )
    lines += [
        "",
        "[2] Post-timeout additional holding curve",
        "horizon | observed | lower% | higher% | avg_additional% | p50_additional% | avg_min% | avg_max%",
        "-" * 100,
    ]
    for horizon, row in (analysis.get("post_timeout", {}).get("horizons", {}) or {}).items():
        add = row.get("additional_return_pct", {}) or {}
        mn = row.get("min_return_pct", {}) or {}
        mx = row.get("max_return_pct", {}) or {}
        lines.append(
            f"{horizon:>7s} | {row.get('observed_count', 0):8d} | {fmt(row.get('lower_rate_pct'), 2):>6s} | "
            f"{fmt(row.get('higher_rate_pct'), 2):>7s} | {fmt(add.get('avg')):>15s} | {fmt(add.get('p50')):>15s} | "
            f"{fmt(mn.get('avg')):>8s} | {fmt(mx.get('avg')):>8s}"
        )
    stop = analysis.get("entry_market_regime_approx", {}).get("stop_loss_focus", {}) or {}
    regimes = stop.get("regime_counts", {}) or {}
    ratios = stop.get("regime_ratios_pct", {}) or {}
    lines += [
        "",
        "[3] Stop-loss entry market state approximation",
        "Method: entry_date 이전 최신 market_history row; actual saved entry context가 아님.",
        f"stop_loss_count: {stop.get('count', 0)}",
        f"bull: {regimes.get('bull', 0)} ({fmt(ratios.get('bull'), 2)}%)",
        f"neutral: {regimes.get('neutral', 0)} ({fmt(ratios.get('neutral'), 2)}%)",
        f"bear: {regimes.get('bear', 0)} ({fmt(ratios.get('bear'), 2)}%)",
        f"missing: {regimes.get('missing', 0)} ({fmt(ratios.get('missing'), 2)}%)",
        "",
        "정식 결론은 BB 이후 entry_market_score/ATR/stop/target/trailing/hash가 저장된 E-1 데이터로 재검증해야 한다.",
    ]
    return "\n".join(lines)


def default_out_dir(run_id: str) -> Path:
    return ANALYSIS_ROOT / run_id


def save_outputs(analysis: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "sell_structure_preview.json"
    txt_path = out_dir / "sell_structure_preview.txt"
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(render_text(analysis), encoding="utf-8")
    return json_path, txt_path


def parse_horizons(raw: str) -> tuple[int, ...]:
    horizons = tuple(sorted({int(x.strip()) for x in str(raw).split(",") if x.strip() and int(x.strip()) > 0}))
    if not horizons:
        raise ValueError("at least one positive horizon required")
    return horizons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E-0 preliminary sell-structure analysis.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--out", help="Output directory outside the completed run. Default: analysis/e0_sell_structure/{run_id}")
    parser.add_argument("--horizons", default="5,10,20", help="Comma-separated trading-day horizons.")
    parser.add_argument("--batch-size", type=int, default=50, help="yfinance batch size.")
    parser.add_argument("--max-tickers", type=int, help="Optional first-N ticker limit for smoke validation.")
    args = parser.parse_args(argv)

    started = time.time()
    horizons = parse_horizons(args.horizons)
    trades, meta = collect_trades(args.run_id)
    tickers = sorted({t["ticker"] for t in trades})
    if args.max_tickers:
        tickers = tickers[: max(1, int(args.max_tickers))]
        allowed = set(tickers)
        trades = [t for t in trades if t["ticker"] in allowed]
    valid_dates = [pd.Timestamp(t["entry_date"]) for t in trades if t.get("entry_date")]
    exit_dates = [pd.Timestamp(t["exit_date"]) for t in trades if t.get("exit_date")]
    if not valid_dates or not exit_dates:
        raise RuntimeError("no valid trade dates")
    start_date = (min(valid_dates) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end_date = (max(exit_dates) + pd.Timedelta(days=max(horizons) * 3 + 10)).strftime("%Y-%m-%d")
    price_histories, price_failures = load_price_histories(tickers, start_date, end_date, batch_size=args.batch_size)
    market_history, score_column = load_market_history_read_only()
    enriched, missing_meta = enrich_trades(trades, price_histories, market_history, score_column, horizons)
    meta.update(
        {
            "analysis_label": "E-0 근사/예비",
            "horizons_trading_days": list(horizons),
            "price_start_date": start_date,
            "price_end_date": end_date,
            "price_history_requested_count": len(tickers),
            "price_history_loaded_count": len(price_histories),
            "price_failure_count": len(price_failures),
            "price_failures_sample": price_failures[:100],
            "market_score_column": score_column,
            "market_history_path": str(MARKET_HISTORY_PATH),
            **missing_meta,
        }
    )
    analysis = analyze_sell_structure(enriched, horizons, meta)
    out_dir = Path(args.out) if args.out else default_out_dir(args.run_id)
    json_path, txt_path = save_outputs(analysis, out_dir)
    print(render_text(analysis))
    print(f"\nelapsed_sec: {time.time() - started:.2f}")
    print(f"json_out: {json_path}")
    print(f"txt_out:  {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
