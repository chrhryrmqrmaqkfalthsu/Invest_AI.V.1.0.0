"""
Central Portfolio no-op gates.

v0 comparison_infra_gate:
  데이터 결정성 + trade 정규화 + row-level comparator + 결과 저장 구조를 검증한다.

v1 engine_noop_gate:
  포트폴리오 날짜축 루프 골격을 legacy_compat/no-op 모드로 실행한다.

v2 fractional_gate:
  동일한 날짜축 루프에서 integer sizing과 fractional sizing을 비교한다.

live_current_proxy_baseline:
  fractional + live hard-stop guard wrapper로 현재 라이브 exit 감시를 재현한다.

T+1-a tplus1_entry_gate:
  신호 결정일(T)과 체결일(T+1 open)을 분리하되, exit는 기존 simulate_exit를 유지한다.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.core.indicators import calc_indicators
from engine.learning.backtest import (
    _attach_full_trade_dump,
    _find_df_index_by_date,
    _full_context_snapshot,
    _lookup_signal_context,
    _news_zscore_window,
    _precompute_topic_feature_map,
    _signal_snapshot,
    run_backtest,
)
from engine.strategies.evaluator import calc_position_size_krw, evaluate_signal
from engine.strategies.exit_simulator import simulate_exit
from engine.strategies.rulebook import Rulebook

MANIFEST_PATH = Path("data/_system/live_universe_lr8d_stage1_manifest.json")
PARAMS_PATH_TMPL = "data/symbols/{ticker}/parameters.json"
OUT_DIR = Path("data/_system/research/central_portfolio/noop_gate")
OUT_DIR_V1 = Path("data/_system/research/central_portfolio/engine_noop_gate_v1")
OUT_DIR_LIVE_PROXY = Path("data/_system/research/central_portfolio/live_current_proxy_baseline")
OUT_DIR_TPLUS1 = Path("data/_system/research/central_portfolio/tplus1_entry_gate")

STRING_FIELDS = [
    "ticker",
    "entry_date",
    "exit_date",
    "exit_reason",
    "entry_reason",
    "exit_signal_reason",
    "exit_snapshot_date",
]
INT_FIELDS = ["trade_index", "holding_days"]
FLOAT_FIELDS = [
    "entry_shares",
    "total_shares",
    "entry_price",
    "exit_price",
    "fill_price_base",
    "trigger_price",
    "pnl_krw",
    "pnl_pct",
    "commission",
    "avg_cost",
    "entry_signal_score",
    "entry_signal_raw_score",
    "entry_signal_threshold",
    "entry_market_adjustment",
    "entry_market_score",
    "entry_sector_score",
    "entry_vix_level",
    "exit_signal_score",
    "exit_signal_raw_score",
    "exit_signal_threshold",
    "exit_market_adjustment",
    "exit_market_score",
    "exit_sector_score",
    "exit_vix_level",
]
ALL_FIELDS = STRING_FIELDS + INT_FIELDS + FLOAT_FIELDS
FLOAT_ABS_TOL = 1e-6


def load_promoted_rulebooks(manifest_path: Path = MANIFEST_PATH) -> list[tuple[str, Rulebook]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: list[tuple[str, Rulebook]] = []
    for ticker in manifest["tickers"]:
        payload = json.loads(Path(PARAMS_PATH_TMPL.format(ticker=ticker)).read_text(encoding="utf-8"))
        out.append((ticker, Rulebook.from_dict(payload["rulebook"])))
    return out


def load_fixed_history(ticker: str, years: int, history_end_date: str) -> Any:
    df = load_ohlcv(ticker, years=years, end_date=history_end_date, use_cache=True, max_retries=1).sort_index()
    return calc_indicators(df).sort_index()


def load_fixed_histories(rulebooks: list[tuple[str, Rulebook]], *, years: int, history_end_date: str) -> dict[str, Any]:
    return {ticker: load_fixed_history(ticker, years=years, history_end_date=history_end_date) for ticker, _ in rulebooks}


def _trade_get(trade: Any, key: str, default=None):
    if isinstance(trade, dict):
        return trade.get(key, default)
    if hasattr(trade, "__dataclass_fields__"):
        return getattr(trade, key, default)
    return getattr(trade, key, default)


def normalize_trade_row(ticker: str, idx: int, trade: Any) -> dict[str, Any]:
    if hasattr(trade, "__dataclass_fields__") and not isinstance(trade, dict):
        trade = asdict(trade)
    row: dict[str, Any] = {"ticker": ticker, "trade_index": idx}
    for field in STRING_FIELDS:
        if field == "ticker":
            continue
        value = _trade_get(trade, field)
        row[field] = "" if value is None else str(value)
    for field in INT_FIELDS:
        if field == "trade_index":
            continue
        value = _trade_get(trade, field)
        row[field] = None if value is None else int(value)
    for field in FLOAT_FIELDS:
        value = _trade_get(trade, field)
        row[field] = None if value is None else float(value)
    decision_date = _trade_get(trade, "decision_date")
    row["decision_date"] = "" if decision_date is None else str(decision_date)
    return row


def normalize_trade_map(rulebooks: list[tuple[str, Rulebook]], trades_by_ticker: dict[str, list[Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ticker, _ in rulebooks:
        for idx, trade in enumerate(trades_by_ticker.get(ticker, [])):
            rows.append(normalize_trade_row(ticker, idx, trade))
    return rows


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


def run_reference_backtests_by_ticker(
    rulebooks: list[tuple[str, Rulebook]],
    histories: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    position_limit_krw: float,
    commission_rate: float,
    warmup: int,
) -> dict[str, list[Any]]:
    return {
        ticker: run_reference_backtest(
            rb,
            histories[ticker],
            start_date,
            end_date,
            position_limit_krw,
            commission_rate,
            warmup,
        )
        for ticker, rb in rulebooks
    }


def _date_series_for_df(df: Any):
    if "date" in df.columns:
        return pd.to_datetime(df["date"])
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, index=df.index)
    return None


def _date_at(date_series: Any, idx: int) -> Optional[pd.Timestamp]:
    if date_series is None:
        return None
    value = date_series.iloc[idx] if hasattr(date_series, "iloc") else date_series[idx]
    return pd.Timestamp(value)


def _global_date_axis(states: dict[str, dict[str, Any]], warmup: int) -> list[pd.Timestamp]:
    dates: set[pd.Timestamp] = set()
    for state in states.values():
        date_series = state["date_series"]
        if date_series is None:
            continue
        for idx in range(max(warmup, 0), state["n"]):
            try:
                dates.add(_date_at(date_series, idx))
            except Exception:
                continue
    return sorted(dates)


def _attach_legacy_trade_metadata(
    *,
    rb: Rulebook,
    df: Any,
    entry_idx: int,
    trade_obj: Any,
    sig: Any,
    cur_sentiment: float,
    cur_market: float,
    cur_sector: float,
    cur_vix: float,
    cur_event_flags: dict,
    cur_topic_features: dict,
    market_score: float,
    sector_score: float,
    vix_level: float,
    market_history_df: Optional[Any],
    sector_name: str,
    ticker_sentiment: Optional[dict],
    topic_feature_map: Optional[dict],
    position_limit_krw: float,
    commission_rate: float,
    cooldown_days: int,
    warmup: int,
    start_date: Optional[str],
    end_date: Optional[str],
    topic_window: int,
    use_llm_events: bool,
) -> tuple[dict[str, Any], Optional[int]]:
    trade = asdict(trade_obj) if hasattr(trade_obj, "__dataclass_fields__") else trade_obj
    if not isinstance(trade, dict):
        return {"raw_trade": trade}, None

    entry_context_full = _full_context_snapshot(
        role="entry",
        df=df,
        idx=entry_idx,
        sig=sig,
        sentiment=cur_sentiment,
        market=cur_market,
        sector=cur_sector,
        vix=cur_vix,
        event_flags=cur_event_flags,
        topic_features=cur_topic_features,
    )
    trade.update(
        _signal_snapshot(
            "entry",
            sig,
            sentiment=cur_sentiment,
            market=cur_market,
            sector=cur_sector,
            vix=cur_vix,
            event_flags=cur_event_flags,
            topic_features=cur_topic_features,
        )
    )

    exit_date = trade.get("exit_date")
    exit_idx = _find_df_index_by_date(df, exit_date)
    exit_context_full: Optional[dict] = None
    try:
        if exit_idx is not None:
            ex_market, ex_sector, ex_vix, ex_sentiment, ex_event_flags, ex_topic_features = _lookup_signal_context(
                df=df,
                idx=exit_idx,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                market_history_df=market_history_df,
                sector_name=sector_name,
                ticker_sentiment=ticker_sentiment,
                topic_feature_map=topic_feature_map,
                use_llm_events=use_llm_events,
            )
            ex_sig = evaluate_signal(
                rb,
                df.iloc[: exit_idx + 1],
                market_score=ex_market,
                sector_score=ex_sector,
                vix_level=ex_vix,
                news_sentiment=ex_sentiment,
                event_flags=ex_event_flags,
                topic_features=ex_topic_features,
            )
            exit_context_full = _full_context_snapshot(
                role="exit",
                df=df,
                idx=exit_idx,
                sig=ex_sig,
                sentiment=ex_sentiment,
                market=ex_market,
                sector=ex_sector,
                vix=ex_vix,
                event_flags=ex_event_flags,
                topic_features=ex_topic_features,
            )
            trade.update(
                _signal_snapshot(
                    "exit",
                    ex_sig,
                    sentiment=ex_sentiment,
                    market=ex_market,
                    sector=ex_sector,
                    vix=ex_vix,
                    event_flags=ex_event_flags,
                    topic_features=ex_topic_features,
                )
            )
            trade["exit_snapshot_date"] = str(pd.Timestamp(df.index[exit_idx]).date())
    except Exception as exc:
        trade["exit_snapshot_error"] = str(exc)

    _attach_full_trade_dump(
        trade=trade,
        rb=rb,
        df=df,
        entry_idx=entry_idx,
        exit_idx=exit_idx,
        entry_context_full=entry_context_full,
        exit_context_full=exit_context_full,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        cooldown_days=cooldown_days,
        warmup=warmup,
        start_date=start_date,
        end_date=end_date,
        topic_window=topic_window,
        use_llm_events=use_llm_events,
    )
    return trade, exit_idx


def _entry_open_on_date(histories: dict[str, Any], ticker: str, entry_date: str) -> Optional[float]:
    df = histories[ticker]
    idx = _find_df_index_by_date(df, entry_date)
    if idx is None:
        return None
    try:
        return float(df.iloc[idx]["Open"])
    except Exception:
        return None


def run_legacy_compat_daily_loop(
    rulebooks: list[tuple[str, Rulebook]],
    histories: dict[str, Any],
    *,
    start_date: str,
    end_date: str,
    position_limit_krw: float,
    commission_rate: float,
    warmup: int,
    cooldown_days: int = 1,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    market_history_df: Optional[Any] = None,
    ticker_sentiments: Optional[dict[str, dict]] = None,
    use_llm_events: bool = True,
    sizing_mode: str = "integer",
    live_hard_stop_guard: bool = False,
    entry_execution_mode: str = "legacy_t_close",
) -> dict[str, list[dict[str, Any]]]:
    if sizing_mode not in {"integer", "fractional"}:
        raise ValueError(f"unsupported sizing_mode={sizing_mode!r}")
    if entry_execution_mode not in {"legacy_t_close", "t_plus_1_open"}:
        raise ValueError(f"unsupported entry_execution_mode={entry_execution_mode!r}")

    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date) if end_date else None
    ticker_sentiments = ticker_sentiments or {}

    states: dict[str, dict[str, Any]] = {}
    for ticker, rb in rulebooks:
        df = histories[ticker]
        ticker_sentiment = ticker_sentiments.get(ticker)
        topic_window = _news_zscore_window(rb)
        states[ticker] = {
            "ticker": ticker,
            "rb": rb,
            "df": df,
            "n": len(df),
            "date_series": _date_series_for_df(df),
            "next_idx": max(warmup, 0),
            "done": False,
            "trades": [],
            "ticker_sentiment": ticker_sentiment,
            "topic_window": topic_window,
            "topic_feature_map": _precompute_topic_feature_map(ticker_sentiment, topic_window),
            "sector_name": getattr(rb, "sector_name", "tech") or "tech",
        }

    for current_ts in _global_date_axis(states, warmup):
        for ticker, _ in rulebooks:
            state = states[ticker]
            if state["done"]:
                continue
            rb = state["rb"]
            df = state["df"]
            date_series = state["date_series"]
            ticker_sentiment = state["ticker_sentiment"]
            topic_feature_map = state["topic_feature_map"]
            sector_name = state["sector_name"]

            while state["next_idx"] < state["n"]:
                idx = int(state["next_idx"])
                if date_series is not None:
                    try:
                        cur_ts = _date_at(date_series, idx)
                        if cur_ts is not None and cur_ts > current_ts:
                            break
                        if start_ts is not None and cur_ts is not None and cur_ts < start_ts:
                            state["next_idx"] = idx + 1
                            continue
                        if end_ts is not None and cur_ts is not None and cur_ts > end_ts:
                            state["done"] = True
                            break
                    except Exception:
                        pass

                sub_df = df.iloc[: idx + 1]
                cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = _lookup_signal_context(
                    df=df,
                    idx=idx,
                    market_score=market_score,
                    sector_score=sector_score,
                    vix_level=vix_level,
                    market_history_df=market_history_df,
                    sector_name=sector_name,
                    ticker_sentiment=ticker_sentiment,
                    topic_feature_map=topic_feature_map,
                    use_llm_events=use_llm_events,
                )
                sig = evaluate_signal(
                    rb,
                    sub_df,
                    market_score=cur_market,
                    sector_score=cur_sector,
                    vix_level=cur_vix,
                    news_sentiment=cur_sentiment,
                    event_flags=cur_event_flags,
                    topic_features=cur_topic_features,
                )
                if not sig.should_buy:
                    state["next_idx"] = idx + 1
                    continue

                entry_exec_idx = idx
                entry_price_override = None
                entry_atr_override = None
                if entry_execution_mode == "t_plus_1_open":
                    fill_idx = idx + 1
                    if fill_idx >= state["n"]:
                        state["done"] = True
                        break
                    fill_ts = _date_at(date_series, fill_idx) if date_series is not None else None
                    if end_ts is not None and fill_ts is not None and fill_ts > end_ts:
                        state["done"] = True
                        break
                    entry_exec_idx = fill_idx
                    entry_price = float(df.iloc[fill_idx]["Open"])
                    try:
                        entry_atr_override = float(df.iloc[idx].get("ATR", entry_price * 0.02))
                    except Exception:
                        entry_atr_override = entry_price * 0.02
                    entry_price_override = entry_price
                else:
                    entry_price = float(df.iloc[idx]["Close"])

                amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
                if sizing_mode == "fractional":
                    shares = amt_krw / entry_price if entry_price > 0 else 0.0
                else:
                    shares = int(amt_krw / entry_price) if entry_price > 0 else 0
                if shares <= 0:
                    state["next_idx"] = idx + 1
                    continue

                trade_obj = simulate_exit(
                    rb,
                    df,
                    entry_exec_idx,
                    shares,
                    position_limit_krw,
                    commission_rate=commission_rate,
                    cur_market_score=cur_market,
                    cur_vix_level=cur_vix,
                    cur_sector_score=cur_sector,
                    fractional_shares=(sizing_mode == "fractional"),
                    disable_add_buy=(sizing_mode == "fractional"),
                    live_hard_stop_guard=live_hard_stop_guard,
                    entry_price_override=entry_price_override,
                    entry_atr_override=entry_atr_override,
                )
                if trade_obj is None:
                    state["done"] = True
                    break

                trade, exit_idx = _attach_legacy_trade_metadata(
                    rb=rb,
                    df=df,
                    entry_idx=entry_exec_idx,
                    trade_obj=trade_obj,
                    sig=sig,
                    cur_sentiment=cur_sentiment,
                    cur_market=cur_market,
                    cur_sector=cur_sector,
                    cur_vix=cur_vix,
                    cur_event_flags=cur_event_flags,
                    cur_topic_features=cur_topic_features,
                    market_score=market_score,
                    sector_score=sector_score,
                    vix_level=vix_level,
                    market_history_df=market_history_df,
                    sector_name=sector_name,
                    ticker_sentiment=ticker_sentiment,
                    topic_feature_map=topic_feature_map,
                    position_limit_krw=position_limit_krw,
                    commission_rate=commission_rate,
                    cooldown_days=cooldown_days,
                    warmup=warmup,
                    start_date=start_date,
                    end_date=end_date,
                    topic_window=state["topic_window"],
                    use_llm_events=use_llm_events,
                )
                try:
                    trade["decision_date"] = str(pd.Timestamp(df.index[idx]).date())
                except Exception:
                    trade["decision_date"] = ""
                state["trades"].append(trade)

                if exit_idx is None:
                    exit_date = trade.get("exit_date") if isinstance(trade, dict) else None
                    exit_idx = _find_df_index_by_date(df, exit_date)
                if exit_idx is None:
                    exit_idx = entry_exec_idx + 1
                state["next_idx"] = max(exit_idx + 1 + cooldown_days, entry_exec_idx + 1)

    return {ticker: list(state["trades"]) for ticker, state in states.items()}


def compare_trade_rows(ref_rows: list[dict], cand_rows: list[dict]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []

    def key(row):
        return (row["ticker"], row["trade_index"])

    ref_map = {key(row): row for row in ref_rows}
    cand_map = {key(row): row for row in cand_rows}
    all_keys = sorted(set(ref_map) | set(cand_map))
    for key_value in all_keys:
        ref = ref_map.get(key_value)
        cand = cand_map.get(key_value)
        if ref is None or cand is None:
            mismatches.append(
                {
                    "ticker": key_value[0],
                    "trade_index": key_value[1],
                    "field": "_row_presence",
                    "ref": "missing" if ref is None else "present",
                    "candidate": "missing" if cand is None else "present",
                    "diff": "row count mismatch",
                }
            )
            continue
        for field in ALL_FIELDS:
            ref_value, cand_value = ref.get(field), cand.get(field)
            if field in FLOAT_FIELDS:
                if ref_value is None and cand_value is None:
                    continue
                if ref_value is None or cand_value is None:
                    ok = False
                    diff = "one-side None"
                else:
                    diff = abs(float(ref_value) - float(cand_value))
                    ok = (not math.isnan(diff)) and diff <= FLOAT_ABS_TOL
                if not ok:
                    mismatches.append(
                        {
                            "ticker": key_value[0],
                            "trade_index": key_value[1],
                            "field": field,
                            "ref": ref_value,
                            "candidate": cand_value,
                            "diff": diff,
                        }
                    )
            else:
                if ref_value != cand_value:
                    mismatches.append(
                        {
                            "ticker": key_value[0],
                            "trade_index": key_value[1],
                            "field": field,
                            "ref": ref_value,
                            "candidate": cand_value,
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


def _fractional_summary(ref_rows: list[dict[str, Any]], cand_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ref_tickers = {row["ticker"] for row in ref_rows}
    cand_tickers = {row["ticker"] for row in cand_rows}
    zero_pnl_count = sum(1 for row in cand_rows if abs(float(row.get("pnl_krw") or 0.0)) < 1e-9)
    non_positive_shares = sum(1 for row in cand_rows if float(row.get("total_shares") or 0.0) <= 0.0)
    min_total_shares = min((float(row.get("total_shares") or 0.0) for row in cand_rows), default=0.0)
    return {
        "ref_ticker_count": len(ref_tickers),
        "candidate_ticker_count": len(cand_tickers),
        "new_ticker_count": len(cand_tickers - ref_tickers),
        "new_tickers": sorted(cand_tickers - ref_tickers),
        "zero_pnl_count": zero_pnl_count,
        "non_positive_shares_count": non_positive_shares,
        "min_total_shares": min_total_shares,
    }


def _common_entry_shift_summary(
    ref_rows: list[dict[str, Any]],
    cand_rows: list[dict[str, Any]],
    histories: dict[str, Any],
) -> dict[str, Any]:
    ref_map = {(row["ticker"], row["trade_index"]): row for row in ref_rows}
    cand_map = {(row["ticker"], row["trade_index"]): row for row in cand_rows}
    common = sorted(set(ref_map) & set(cand_map))
    common_same_entry_date = 0
    common_later_entry_date = 0
    common_not_later_entry_date = 0
    decision_entry_same = 0
    decision_entry_later = 0
    decision_entry_not_later = 0
    open_price_mismatch = 0
    samples: list[dict[str, Any]] = []

    for row in cand_rows:
        decision_date = str(row.get("decision_date") or "")
        entry_date = str(row.get("entry_date") or "")
        if entry_date == decision_date:
            decision_entry_same += 1
        if entry_date > decision_date:
            decision_entry_later += 1
        else:
            decision_entry_not_later += 1
        expected_open = _entry_open_on_date(histories, row["ticker"], entry_date)
        if expected_open is None or abs(float(row["entry_price"]) - expected_open) > FLOAT_ABS_TOL:
            open_price_mismatch += 1

    for key in common:
        ref = ref_map[key]
        cand = cand_map[key]
        if cand["entry_date"] == ref["entry_date"]:
            common_same_entry_date += 1
        if cand["entry_date"] > ref["entry_date"]:
            common_later_entry_date += 1
        else:
            common_not_later_entry_date += 1
        expected_open = _entry_open_on_date(histories, cand["ticker"], cand["entry_date"])
        if len(samples) < 8:
            samples.append(
                {
                    "ticker": key[0],
                    "trade_index": key[1],
                    "candidate_decision_date": cand.get("decision_date", ""),
                    "ref_entry_date": ref["entry_date"],
                    "ref_entry_price": ref["entry_price"],
                    "candidate_entry_date": cand["entry_date"],
                    "candidate_entry_price": cand["entry_price"],
                    "candidate_expected_open": expected_open,
                }
            )
    return {
        "common_trade_key_count": len(common),
        "common_candidate_entry_date_later_count": common_later_entry_date,
        "common_candidate_entry_date_same_count": common_same_entry_date,
        "common_candidate_entry_date_not_later_count": common_not_later_entry_date,
        "candidate_decision_entry_later_count": decision_entry_later,
        "candidate_decision_entry_same_count": decision_entry_same,
        "candidate_decision_entry_not_later_count": decision_entry_not_later,
        "candidate_entry_open_mismatch_count": open_price_mismatch,
        "entry_shift_samples": samples,
    }


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
    rulebooks = load_promoted_rulebooks()
    histories = load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)
    ref_trades_by_ticker = run_reference_backtests_by_ticker(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
    )
    cand_trades_by_ticker = run_reference_backtests_by_ticker(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
    )
    ref_rows = normalize_trade_map(rulebooks, ref_trades_by_ticker)
    cand_rows = normalize_trade_map(rulebooks, cand_trades_by_ticker)
    mismatches = compare_trade_rows(ref_rows, cand_rows)
    summary = {
        "gate": "comparison_infra_gate_v0",
        "self_vs_self": True,
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "tickers": [ticker for ticker, _ in rulebooks],
        "ref_trade_count": len(ref_rows),
        "candidate_trade_count": len(cand_rows),
        "mismatch_count": len(mismatches),
        "passed": len(mismatches) == 0 and len(ref_rows) == len(cand_rows),
    }
    write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir)
    return summary


def run_engine_noop_gate_v1(
    start_date: str,
    end_date: str,
    history_end_date: str,
    position_limit_krw: float = 120000.0,
    commission_rate: float = 0.0005,
    warmup: int = 200,
    years: int = 3,
    out_dir: Path = OUT_DIR_V1,
) -> dict[str, Any]:
    rulebooks = load_promoted_rulebooks()
    histories = load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)
    ref_trades_by_ticker = run_reference_backtests_by_ticker(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
    )
    cand_trades_by_ticker = run_legacy_compat_daily_loop(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sizing_mode="integer",
    )
    ref_rows = normalize_trade_map(rulebooks, ref_trades_by_ticker)
    cand_rows = normalize_trade_map(rulebooks, cand_trades_by_ticker)
    mismatches = compare_trade_rows(ref_rows, cand_rows)
    summary = {
        "gate": "engine_noop_gate_v1",
        "legacy_compat_daily_loop": True,
        "sizing_mode": "integer",
        "entry_execution_mode": "legacy_t_close",
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "tickers": [ticker for ticker, _ in rulebooks],
        "ref_trade_count": len(ref_rows),
        "candidate_trade_count": len(cand_rows),
        "mismatch_count": len(mismatches),
        "passed": len(mismatches) == 0 and len(ref_rows) == len(cand_rows),
    }
    write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir)
    return summary


def run_fractional_gate_v2(
    start_date: str,
    end_date: str,
    history_end_date: str,
    position_limit_krw: float = 30.0,
    commission_rate: float = 0.0005,
    warmup: int = 200,
    years: int = 3,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    rulebooks = load_promoted_rulebooks()
    histories = load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)
    ref_trades_by_ticker = run_legacy_compat_daily_loop(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sizing_mode="integer",
    )
    cand_trades_by_ticker = run_legacy_compat_daily_loop(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sizing_mode="fractional",
    )
    ref_rows = normalize_trade_map(rulebooks, ref_trades_by_ticker)
    cand_rows = normalize_trade_map(rulebooks, cand_trades_by_ticker)
    mismatches = compare_trade_rows(ref_rows, cand_rows)
    fractional_stats = _fractional_summary(ref_rows, cand_rows)
    passed = (
        len(cand_rows) > len(ref_rows)
        and fractional_stats["candidate_ticker_count"] >= fractional_stats["ref_ticker_count"]
        and fractional_stats["zero_pnl_count"] == 0
        and fractional_stats["non_positive_shares_count"] == 0
    )
    summary = {
        "gate": "fractional_gate_v2",
        "reference_sizing_mode": "integer",
        "candidate_sizing_mode": "fractional",
        "fractional_shares": True,
        "disable_add_buy": True,
        "entry_execution_mode": "legacy_t_close",
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "tickers": [ticker for ticker, _ in rulebooks],
        "ref_trade_count": len(ref_rows),
        "candidate_trade_count": len(cand_rows),
        "mismatch_count": len(mismatches),
        "passed": passed,
        **fractional_stats,
    }
    write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir)
    return summary


def run_live_current_proxy_baseline(
    start_date: str,
    end_date: str,
    history_end_date: str,
    position_limit_krw: float = 30.0,
    commission_rate: float = 0.0005,
    warmup: int = 200,
    years: int = 3,
    out_dir: Path = OUT_DIR_LIVE_PROXY,
) -> dict[str, Any]:
    rulebooks = load_promoted_rulebooks()
    histories = load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)
    ref_trades_by_ticker = run_legacy_compat_daily_loop(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sizing_mode="fractional",
        live_hard_stop_guard=False,
    )
    cand_trades_by_ticker = run_legacy_compat_daily_loop(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sizing_mode="fractional",
        live_hard_stop_guard=True,
    )
    ref_rows = normalize_trade_map(rulebooks, ref_trades_by_ticker)
    cand_rows = normalize_trade_map(rulebooks, cand_trades_by_ticker)
    mismatches = compare_trade_rows(ref_rows, cand_rows)
    guard_stop_loss_rows = [row for row in cand_rows if row.get("exit_reason") == "stop_loss"]
    guard_affected_tickers = sorted({row["ticker"] for row in guard_stop_loss_rows})
    mismatch_tickers = sorted({row.get("ticker") for row in mismatches if row.get("ticker")})
    mismatch_tickers_without_guard = sorted(set(mismatch_tickers) - set(guard_affected_tickers))
    timing_mismatch_count = sum(
        1 for row in mismatches if row.get("field") in {"entry_date", "exit_date", "exit_reason"}
    )
    summary = {
        "gate": "live_current_proxy_baseline",
        "reference_mode": "fractional_guard_off",
        "candidate_mode": "fractional_live_hard_stop_guard_on",
        "fractional_shares": True,
        "disable_add_buy": True,
        "live_hard_stop_guard": True,
        "entry_execution_mode": "legacy_t_close",
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "tickers": [ticker for ticker, _ in rulebooks],
        "ref_trade_count": len(ref_rows),
        "candidate_trade_count": len(cand_rows),
        "mismatch_count": len(mismatches),
        "timing_mismatch_count": timing_mismatch_count,
        "candidate_stop_loss_count": len(guard_stop_loss_rows),
        "guard_affected_tickers": guard_affected_tickers,
        "mismatch_tickers": mismatch_tickers,
        "mismatch_tickers_without_guard": mismatch_tickers_without_guard,
        "passed": len(cand_rows) > 0 and len(guard_stop_loss_rows) > 0 and not mismatch_tickers_without_guard,
    }
    write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir)
    return summary


def run_tplus1_entry_gate(
    start_date: str,
    end_date: str,
    history_end_date: str,
    position_limit_krw: float = 30.0,
    commission_rate: float = 0.0005,
    warmup: int = 200,
    years: int = 3,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    rulebooks = load_promoted_rulebooks()
    histories = load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)
    ref_trades_by_ticker = run_legacy_compat_daily_loop(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sizing_mode="fractional",
        live_hard_stop_guard=False,
        entry_execution_mode="legacy_t_close",
    )
    cand_trades_by_ticker = run_legacy_compat_daily_loop(
        rulebooks,
        histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        sizing_mode="fractional",
        live_hard_stop_guard=False,
        entry_execution_mode="t_plus_1_open",
    )
    ref_rows = normalize_trade_map(rulebooks, ref_trades_by_ticker)
    cand_rows = normalize_trade_map(rulebooks, cand_trades_by_ticker)
    mismatches = compare_trade_rows(ref_rows, cand_rows)
    entry_shift = _common_entry_shift_summary(ref_rows, cand_rows, histories)
    summary = {
        "gate": "tplus1_entry_gate",
        "reference_mode": "fractional_legacy_t_close",
        "candidate_mode": "fractional_t_plus_1_open",
        "fractional_shares": True,
        "disable_add_buy": True,
        "live_hard_stop_guard": False,
        "entry_execution_mode": "t_plus_1_open",
        "entry_atr_source": "decision_day_atr",
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "tickers": [ticker for ticker, _ in rulebooks],
        "ref_trade_count": len(ref_rows),
        "candidate_trade_count": len(cand_rows),
        "mismatch_count": len(mismatches),
        **entry_shift,
    }
    summary["passed"] = (
        len(cand_rows) > 0
        and entry_shift["common_trade_key_count"] > 0
        and entry_shift["candidate_decision_entry_same_count"] == 0
        and entry_shift["candidate_decision_entry_not_later_count"] == 0
        and entry_shift["candidate_entry_open_mismatch_count"] == 0
    )
    write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir)
    return summary
