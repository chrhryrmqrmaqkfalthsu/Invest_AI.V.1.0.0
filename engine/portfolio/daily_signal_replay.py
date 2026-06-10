"""Daily signal replay for stage2 lot-level capital probes.

Research-only module. It does not modify stage2 trades, live trading code,
PositionManager, or evaluate_signal().

Safety invariants
-----------------
* Rulebooks are loaded only from stage2 artifacts (topn_rulebooks.jsonl).
  Current data/symbols/{ticker}/parameters.json fallback is forbidden.
* Decision date is the canonical replay axis. entry_signal_date, entry_fill_date,
  and exit_date are recorded separately.
* Each replay call passes df.iloc[:T+1] to evaluate_signal(), matching backtest
  look-ahead discipline.
* Entry replay must match logged entry strength within strict tolerance before a
  daily time series is considered valid.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from engine.core.indicators import calc_indicators
from engine.learning.backtest import (
    FEATURE_LAG_DAYS,
    FEATURE_LAG_MAX_AGE_DAYS,
    _lookup_signal_context,
    _news_zscore_window,
    _precompute_topic_feature_map,
)
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook

STAGE2_DIR = Path("data/_system/research/honest_full_6174_20260610/stage2_batch_000")
DEFAULT_TRADES_JSONL = STAGE2_DIR / "trades.jsonl"
DEFAULT_RULEBOOKS_JSONL = STAGE2_DIR / "topn_rulebooks.jsonl"
DEFAULT_OHLCV_CACHE = Path("data/_system/research/honest_full_6174_20260610/stage0/ohlcv_cache")
OUT_DIR = Path("data/_system/research/central_portfolio/daily_signal_replay")
ENTRY_DIFF_ABS_TOL = 1e-6
ENTRY_DIFF_PCT_TOL = 0.01
REQUIRED_TRADE_FIELDS = {
    "ticker",
    "member_hash",
    "rulebook_hash",
    "entry_signal_date",
    "entry_fill_date",
    "exit_date",
    "entry_signal_score",
    "entry_signal_threshold",
}


@dataclass(frozen=True)
class ReplayConfig:
    max_lots: int = 20
    max_daily_rows_per_lot: int = 0
    full_run: bool = False
    fail_on_entry_mismatch: bool = True


@dataclass(frozen=True)
class ReplayInputStatus:
    component: str
    reproducible: bool
    source: str
    treatment: str
    blocker: bool = False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _to_date(value: Any) -> pd.Timestamp:
    return pd.Timestamp(str(value)[:10]).normalize()


def _read_jsonl(path: Path, limit: Optional[int] = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not Path(path).exists():
        return rows
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def build_rulebook_map(rulebooks_jsonl: Path = DEFAULT_RULEBOOKS_JSONL) -> dict[tuple[str, str], Rulebook]:
    """Load stage2 rulebooks keyed by (member_hash, rulebook_hash).

    No fallback is allowed. Missing artifact means replay is not verifiable.
    """
    rb_map: dict[tuple[str, str], Rulebook] = {}
    for row in _read_jsonl(Path(rulebooks_jsonl)):
        rb_dict = row.get("rulebook")
        member_hash = str(row.get("member_hash") or "").strip()
        rulebook_hash = str(row.get("rulebook_hash") or "").strip()
        if not isinstance(rb_dict, dict) or not member_hash or not rulebook_hash:
            continue
        rb_map[(member_hash, rulebook_hash)] = Rulebook.from_dict(dict(rb_dict))
    return rb_map


def _trade_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("member_hash") or "").strip(), str(row.get("rulebook_hash") or "").strip()


def _lot_id(row: dict[str, Any], index: int) -> str:
    existing = str(row.get("lot_id") or "").strip()
    if existing:
        return existing
    ticker = str(row.get("ticker") or "").upper().strip()
    mh = str(row.get("member_hash") or "")[:12]
    return f"{ticker}:{mh}:{row.get('entry_signal_date')}:{row.get('entry_fill_date')}:{index}"


def _valid_trade(row: dict[str, Any]) -> bool:
    if not REQUIRED_TRADE_FIELDS.issubset(row):
        return False
    threshold = _to_float(row.get("entry_signal_threshold"), 0.0)
    if threshold <= 0:
        return False
    try:
        return _to_date(row.get("exit_date")) >= _to_date(row.get("entry_signal_date"))
    except Exception:
        return False


def load_stage2_lots(trades_jsonl: Path = DEFAULT_TRADES_JSONL, *, limit: Optional[int] = None) -> list[dict[str, Any]]:
    rows = []
    for i, row in enumerate(_read_jsonl(Path(trades_jsonl), limit=limit)):
        if not _valid_trade(row):
            continue
        r = dict(row)
        r["ticker"] = str(r.get("ticker") or "").upper().strip()
        r["lot_id"] = _lot_id(r, i)
        rows.append(r)
    return rows


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    rename = {}
    for col in data.columns:
        low = str(col).lower()
        if low == "open":
            rename[col] = "Open"
        elif low == "high":
            rename[col] = "High"
        elif low == "low":
            rename[col] = "Low"
        elif low == "close":
            rename[col] = "Close"
        elif low == "volume":
            rename[col] = "Volume"
        elif low in {"date", "datetime"}:
            rename[col] = "Date"
    data = data.rename(columns=rename)
    if "Date" in data.columns:
        data["_dt"] = pd.to_datetime(data["Date"]).dt.normalize()
        data = data.set_index("_dt")
    else:
        data.index = pd.to_datetime(data.index).normalize()
    if not {"Open", "High", "Low", "Close", "Volume"}.issubset(data.columns):
        raise ValueError("OHLCV missing required columns")
    if "MACD" not in data.columns or "RSI" not in data.columns or "Aligned_bull" not in data.columns:
        data = calc_indicators(data[["Open", "High", "Low", "Close", "Volume"]])
    return data.sort_index()


def load_ohlcv_for_ticker(ticker: str, cache_dir: Path = DEFAULT_OHLCV_CACHE) -> Optional[pd.DataFrame]:
    t = str(ticker or "").upper().strip()
    for suffix in [".pkl", ".parquet", ".csv"]:
        path = Path(cache_dir) / f"{t}{suffix}"
        if not path.exists():
            continue
        if suffix == ".pkl":
            df = pd.read_pickle(path)
        elif suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path)
        return _normalize_ohlcv(df)
    return None


def _idx_for_date(df: pd.DataFrame, date_value: Any) -> Optional[int]:
    d = _to_date(date_value)
    index = pd.DatetimeIndex(pd.to_datetime(df.index).normalize())
    matches = index.get_indexer([d], method=None)
    if len(matches) and matches[0] >= 0:
        return int(matches[0])
    # Prefer previous trading day, never future.
    pos = index.searchsorted(d, side="right") - 1
    if pos >= 0:
        return int(pos)
    return None


def _strength(score: float, threshold: float) -> Optional[float]:
    if threshold <= 0:
        return None
    return float(score) / float(threshold)


def component_reproducibility_report(*, has_ohlcv: bool, has_market_history: bool, has_ticker_sentiment: bool) -> list[dict[str, Any]]:
    statuses = [
        ReplayInputStatus("OHLCV/technical", has_ohlcv, "stage0/ohlcv_cache + calc_indicators", "required; blocker if missing", blocker=not has_ohlcv),
        ReplayInputStatus("market_history", has_market_history, "engine.market.context.get_market_history cache", f"lagged by FEATURE_LAG_DAYS={FEATURE_LAG_DAYS}", blocker=False),
        ReplayInputStatus("sector", has_market_history, "market_history sector_{sector_name}", "fallback to sector_score=50 if absent, same as backtest", blocker=False),
        ReplayInputStatus("vix", has_market_history, "market_history vix", "fallback to vix=18 if absent, same as backtest", blocker=False),
        ReplayInputStatus("events", has_market_history, "market_history event flag columns", "zero flags if absent, same as backtest", blocker=False),
        ReplayInputStatus("ticker_sentiment", has_ticker_sentiment, "data/_system/ticker_sentiment/{ticker}_daily.csv", f"lagged by FEATURE_LAG_DAYS={FEATURE_LAG_DAYS}, max_age={FEATURE_LAG_MAX_AGE_DAYS}", blocker=False),
        ReplayInputStatus("news_topics", has_ticker_sentiment, "precompute_topic_features(ticker_sentiment, rb.news_zscore_window)", f"lagged by FEATURE_LAG_DAYS={FEATURE_LAG_DAYS}, max_age={FEATURE_LAG_MAX_AGE_DAYS}", blocker=False),
    ]
    return [status.__dict__ for status in statuses]


def _replay_signal_for_idx(
    rb: Rulebook,
    df: pd.DataFrame,
    idx: int,
    *,
    market_history_df: Optional[pd.DataFrame],
    ticker_sentiment: Optional[dict],
    topic_feature_map: Optional[dict],
) -> tuple[Any, dict[str, Any]]:
    cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = _lookup_signal_context(
        df=df,
        idx=idx,
        market_score=50.0,
        sector_score=50.0,
        vix_level=18.0,
        market_history_df=market_history_df,
        sector_name=str(getattr(rb, "sector_name", "tech") or "tech"),
        ticker_sentiment=ticker_sentiment,
        topic_feature_map=topic_feature_map,
        use_llm_events=True,
    )
    sig = evaluate_signal(
        rb,
        df.iloc[: idx + 1],
        market_score=cur_market,
        sector_score=cur_sector,
        vix_level=cur_vix,
        news_sentiment=cur_sentiment,
        event_flags=cur_event_flags,
        topic_features=cur_topic_features,
    )
    context = {
        "market_score": cur_market,
        "sector_score": cur_sector,
        "vix_level": cur_vix,
        "news_sentiment": cur_sentiment,
        "event_flags": cur_event_flags,
        "topic_features": cur_topic_features,
    }
    return sig, context


def _logged_entry_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_score": row.get("entry_market_score"),
        "sector_score": row.get("entry_sector_score"),
        "vix_level": row.get("entry_vix_level"),
        "news_sentiment": row.get("entry_news_sentiment"),
        "event_flags": row.get("entry_event_flags") or {},
        "topic_features": row.get("entry_topic_features") or {},
    }


def _numeric_diff_dict(replayed: dict[str, Any], logged: dict[str, Any]) -> dict[str, Any]:
    keys = sorted(set(replayed) | set(logged))
    out: dict[str, Any] = {}
    for key in keys:
        rv = replayed.get(key, 0.0)
        lv = logged.get(key, 0.0)
        if isinstance(rv, (int, float)) or isinstance(lv, (int, float)):
            out[key] = _to_float(rv) - _to_float(lv)
        elif rv != lv:
            out[key] = {"replayed": rv, "logged": lv}
    return out


def _context_diff(replayed: dict[str, Any], logged: dict[str, Any]) -> dict[str, Any]:
    return {
        "market_score": _to_float(replayed.get("market_score")) - _to_float(logged.get("market_score")),
        "sector_score": _to_float(replayed.get("sector_score")) - _to_float(logged.get("sector_score")),
        "vix_level": _to_float(replayed.get("vix_level")) - _to_float(logged.get("vix_level")),
        "news_sentiment": _to_float(replayed.get("news_sentiment")) - _to_float(logged.get("news_sentiment")),
        "event_flags": _numeric_diff_dict(dict(replayed.get("event_flags") or {}), dict(logged.get("event_flags") or {})),
        "topic_features": _numeric_diff_dict(dict(replayed.get("topic_features") or {}), dict(logged.get("topic_features") or {})),
    }


def replay_one_lot(
    row: dict[str, Any],
    rb: Rulebook,
    df: pd.DataFrame,
    *,
    market_history_df: Optional[pd.DataFrame],
    ticker_sentiment: Optional[dict],
    max_daily_rows: int = 0,
) -> dict[str, Any]:
    topic_window = _news_zscore_window(rb)
    topic_feature_map = _precompute_topic_feature_map(ticker_sentiment, topic_window)
    entry_idx = _idx_for_date(df, row.get("entry_signal_date"))
    if entry_idx is None:
        raise ValueError(f"entry_signal_date not found in df: {row.get('entry_signal_date')}")
    entry_sig, entry_ctx = _replay_signal_for_idx(
        rb,
        df,
        entry_idx,
        market_history_df=market_history_df,
        ticker_sentiment=ticker_sentiment,
        topic_feature_map=topic_feature_map,
    )
    logged_strength = _strength(_to_float(row.get("entry_signal_score")), _to_float(row.get("entry_signal_threshold")))
    replay_strength = _strength(float(entry_sig.score), float(entry_sig.threshold))
    if logged_strength is None or replay_strength is None:
        diff_abs = None
        diff_pct = None
    else:
        diff_abs = abs(replay_strength - logged_strength)
        diff_pct = diff_abs / max(abs(logged_strength), 1e-12) * 100.0

    logged_components = dict(row.get("entry_signal_components") or {})
    logged_context = _logged_entry_context(row)
    entry_record = {
        "ticker": row.get("ticker"),
        "lot_id": row.get("lot_id"),
        "rulebook_hash": row.get("rulebook_hash"),
        "member_hash": row.get("member_hash"),
        "decision_date": str(df.index[entry_idx].date()),
        "entry_signal_date": str(row.get("entry_signal_date") or ""),
        "entry_fill_date": str(row.get("entry_fill_date") or ""),
        "exit_date": str(row.get("exit_date") or ""),
        "current_score": float(entry_sig.score),
        "current_threshold": float(entry_sig.threshold),
        "current_strength": replay_strength,
        "entry_strength_logged": logged_strength,
        "entry_strength_replayed": replay_strength,
        "entry_strength_diff_abs": diff_abs,
        "entry_strength_diff_pct": diff_pct,
        "strength_decay_pct": 0.0 if replay_strength is not None else None,
        "signal_valid": replay_strength is not None,
        "raw_score": float(entry_sig.raw_score),
        "market_adjustment": float(entry_sig.market_adjustment),
        "components": dict(entry_sig.components),
        "context": entry_ctx,
        "reasons": list(entry_sig.reasons),
        "logged_score": _to_float(row.get("entry_signal_score")),
        "logged_threshold": _to_float(row.get("entry_signal_threshold")),
        "logged_raw_score": _to_float(row.get("entry_signal_raw_score")),
        "logged_market_adjustment": _to_float(row.get("entry_market_adjustment")),
        "logged_components": logged_components,
        "logged_context": logged_context,
        "component_diffs": _numeric_diff_dict(dict(entry_sig.components), logged_components),
        "context_diffs": _context_diff(entry_ctx, logged_context),
        "score_diff": float(entry_sig.score) - _to_float(row.get("entry_signal_score")),
        "raw_score_diff": float(entry_sig.raw_score) - _to_float(row.get("entry_signal_raw_score")),
        "market_adjustment_diff": float(entry_sig.market_adjustment) - _to_float(row.get("entry_market_adjustment")),
    }

    daily_records: list[dict[str, Any]] = []
    start_idx = entry_idx
    end_idx = _idx_for_date(df, row.get("exit_date"))
    if end_idx is None:
        end_idx = len(df) - 1
    end_idx = min(end_idx, len(df) - 1)
    limit_count = 0
    for i in range(start_idx, end_idx + 1):
        sig, ctx = _replay_signal_for_idx(
            rb,
            df,
            i,
            market_history_df=market_history_df,
            ticker_sentiment=ticker_sentiment,
            topic_feature_map=topic_feature_map,
        )
        cur_strength = _strength(float(sig.score), float(sig.threshold))
        valid = cur_strength is not None
        decay = None
        if replay_strength is not None and cur_strength is not None:
            decay = (replay_strength - cur_strength) / max(abs(replay_strength), 1e-12) * 100.0
        price_path_proxy = None
        try:
            entry_price = _to_float(row.get("entry_price"), 0.0)
            cur_close = _to_float(df.iloc[i].get("Close"), 0.0)
            if entry_price > 0 and cur_close > 0:
                price_path_proxy = (entry_price - cur_close) / entry_price * 100.0
        except Exception:
            price_path_proxy = None
        daily_records.append({
            "ticker": row.get("ticker"),
            "lot_id": row.get("lot_id"),
            "rulebook_hash": row.get("rulebook_hash"),
            "member_hash": row.get("member_hash"),
            "decision_date": str(df.index[i].date()),
            "entry_signal_date": str(row.get("entry_signal_date") or ""),
            "entry_fill_date": str(row.get("entry_fill_date") or ""),
            "exit_date": str(row.get("exit_date") or ""),
            "current_score": float(sig.score),
            "current_threshold": float(sig.threshold),
            "current_strength": cur_strength,
            "entry_strength_logged": logged_strength,
            "entry_strength_replayed": replay_strength,
            "entry_strength_diff_abs": diff_abs,
            "entry_strength_diff_pct": diff_pct,
            "strength_decay_pct": decay,
            "signal_valid": valid,
            "price_path_proxy_baseline": price_path_proxy,
            "raw_score": float(sig.raw_score),
            "market_adjustment": float(sig.market_adjustment),
            "components": dict(sig.components),
            "context": ctx,
            "reasons": list(sig.reasons),
        })
        limit_count += 1
        if max_daily_rows and limit_count >= max_daily_rows:
            break
    return {"entry": entry_record, "daily": daily_records}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    flat_rows = []
    for row in rows:
        flat = dict(row)
        for key in ["components", "context", "reasons", "logged_components", "logged_context", "component_diffs", "context_diffs"]:
            flat[key] = json.dumps(flat.get(key, {} if key != "reasons" else []), ensure_ascii=False)
        flat_rows.append(flat)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(flat_rows[0].keys()))
        writer.writeheader()
        writer.writerows(flat_rows)


def entry_diff_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    vals_abs = [float(x["entry_strength_diff_abs"]) for x in entries if x.get("entry_strength_diff_abs") is not None]
    vals_pct = [float(x["entry_strength_diff_pct"]) for x in entries if x.get("entry_strength_diff_pct") is not None]
    def stats(vals: list[float]) -> dict[str, Any]:
        if not vals:
            return {"count": 0}
        s = sorted(vals)
        return {
            "count": len(vals),
            "min": s[0],
            "p50": s[len(s)//2],
            "p95": s[int(0.95 * (len(s)-1))],
            "max": s[-1],
        }
    fail = [x for x in entries if not _entry_match_ok(x)]
    return {
        "abs": stats(vals_abs),
        "pct": stats(vals_pct),
        "fail_count": len(fail),
        "pass_count": len(entries) - len(fail),
        "tolerance": {"abs": ENTRY_DIFF_ABS_TOL, "pct": ENTRY_DIFF_PCT_TOL},
        "failed_samples": fail[:10],
    }


def _entry_match_ok(entry: dict[str, Any]) -> bool:
    diff_abs = entry.get("entry_strength_diff_abs")
    diff_pct = entry.get("entry_strength_diff_pct")
    if diff_abs is None or diff_pct is None:
        return False
    return float(diff_abs) <= ENTRY_DIFF_ABS_TOL or float(diff_pct) <= ENTRY_DIFF_PCT_TOL


def proxy_disagreement_rate(daily_rows: list[dict[str, Any]], *, decay_threshold_pct: float = 15.0, proxy_threshold_pct: float = 3.0) -> dict[str, Any]:
    comparable = []
    for row in daily_rows:
        d = row.get("strength_decay_pct")
        p = row.get("price_path_proxy_baseline")
        if d is None or p is None:
            continue
        signal_weak = float(d) >= decay_threshold_pct
        price_weak = float(p) >= proxy_threshold_pct
        comparable.append((signal_weak, price_weak))
    if not comparable:
        return {"count": 0, "disagreement_rate_pct": None}
    disagree = sum(1 for a, b in comparable if a != b)
    return {
        "count": len(comparable),
        "disagreement_count": disagree,
        "disagreement_rate_pct": disagree / len(comparable) * 100.0,
        "decay_threshold_pct": decay_threshold_pct,
        "price_proxy_threshold_pct": proxy_threshold_pct,
    }


def dry_run_plan(
    *,
    trades_jsonl: Path = DEFAULT_TRADES_JSONL,
    rulebooks_jsonl: Path = DEFAULT_RULEBOOKS_JSONL,
    ohlcv_cache: Path = DEFAULT_OHLCV_CACHE,
    limit: int = 20,
) -> dict[str, Any]:
    lots = load_stage2_lots(trades_jsonl, limit=limit)
    rb_map = build_rulebook_map(rulebooks_jsonl)
    missing_rb = [row for row in lots if _trade_key(row) not in rb_map]
    tickers = sorted({row["ticker"] for row in lots})
    ohlcv_available = {ticker: (Path(ohlcv_cache) / f"{ticker}.pkl").exists() for ticker in tickers}
    sentiment_available = {ticker: bool(load_ticker_sentiment(ticker)) for ticker in tickers[: min(20, len(tickers))]}
    return {
        "gate": "daily_signal_replay_dry_plan",
        "stage2_guard": "full_replay_not_executed_while_stage2_running",
        "trades_jsonl": str(trades_jsonl),
        "rulebooks_jsonl": str(rulebooks_jsonl),
        "ohlcv_cache": str(ohlcv_cache),
        "lots_sampled": len(lots),
        "rulebooks_loaded": len(rb_map),
        "missing_rulebook_count": len(missing_rb),
        "fallback_used": False,
        "fallback_policy": "forbidden; missing stage2 rulebook_hash is blocker",
        "ticker_count": len(tickers),
        "ohlcv_available": ohlcv_available,
        "ticker_sentiment_available_sample": sentiment_available,
        "feature_lag_days": FEATURE_LAG_DAYS,
        "feature_lag_max_age_days": FEATURE_LAG_MAX_AGE_DAYS,
        "date_axis": "decision_date; entry_signal_date/entry_fill_date/exit_date recorded separately",
        "join_key": "ticker / lot_id / rulebook_hash / decision_date",
        "entry_replay_tolerance": {"abs": ENTRY_DIFF_ABS_TOL, "pct": ENTRY_DIFF_PCT_TOL},
        "will_not_execute_full_replay_in_dry_run": True,
    }


def run_daily_signal_replay(
    *,
    trades_jsonl: Path = DEFAULT_TRADES_JSONL,
    rulebooks_jsonl: Path = DEFAULT_RULEBOOKS_JSONL,
    ohlcv_cache: Path = DEFAULT_OHLCV_CACHE,
    out_dir: Path = OUT_DIR,
    config: ReplayConfig = ReplayConfig(),
) -> dict[str, Any]:
    lots = load_stage2_lots(trades_jsonl, limit=config.max_lots if not config.full_run else None)
    rb_map = build_rulebook_map(rulebooks_jsonl)
    if not rb_map:
        raise RuntimeError(f"No stage2 rulebooks loaded from {rulebooks_jsonl}; fallback forbidden")
    missing = [row for row in lots if _trade_key(row) not in rb_map]
    if missing:
        raise RuntimeError(f"Missing stage2 rulebooks for {len(missing)} lots; fallback forbidden")

    market_history_df = get_market_history(years=7)
    entries: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    component_reports: dict[str, list[dict[str, Any]]] = {}
    mismatch_samples: list[dict[str, Any]] = []
    for row in lots:
        ticker = row["ticker"]
        rb = rb_map[_trade_key(row)]
        df = load_ohlcv_for_ticker(ticker, ohlcv_cache)
        if df is None:
            raise RuntimeError(f"OHLCV missing for {ticker}; replay blocker")
        ticker_sentiment = load_ticker_sentiment(ticker) or {}
        component_reports[ticker] = component_reproducibility_report(
            has_ohlcv=True,
            has_market_history=market_history_df is not None and not market_history_df.empty,
            has_ticker_sentiment=bool(ticker_sentiment),
        )
        replayed = replay_one_lot(
            row,
            rb,
            df,
            market_history_df=market_history_df,
            ticker_sentiment=ticker_sentiment,
            max_daily_rows=config.max_daily_rows_per_lot,
        )
        entry = replayed["entry"]
        entries.append(entry)
        if not _entry_match_ok(entry):
            mismatch_samples.append(entry)
        if mismatch_samples and config.fail_on_entry_mismatch:
            break
        daily_rows.extend(replayed["daily"])

    stats = entry_diff_stats(entries)
    summary = {
        "gate": "daily_signal_replay",
        "trades_jsonl": str(trades_jsonl),
        "rulebooks_jsonl": str(rulebooks_jsonl),
        "out_dir": str(out_dir),
        "full_run": bool(config.full_run),
        "fallback_used": False,
        "fallback_policy": "forbidden; stage2 topn_rulebooks only",
        "lots_loaded": len(lots),
        "lots_replayed": len(entries),
        "daily_rows": len(daily_rows),
        "feature_lag_days": FEATURE_LAG_DAYS,
        "feature_lag_max_age_days": FEATURE_LAG_MAX_AGE_DAYS,
        "entry_diff_stats": stats,
        "entry_replay_passed": stats["fail_count"] == 0,
        "component_reproducibility_by_ticker_sample": dict(list(component_reports.items())[:10]),
        "proxy_disagreement": proxy_disagreement_rate(daily_rows),
        "blocked_reason": "entry_strength_mismatch" if stats["fail_count"] else "",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(out_dir / "entry_replay.jsonl", entries)
    _write_csv(out_dir / "entry_replay.csv", entries)
    if stats["fail_count"] == 0:
        _write_jsonl(out_dir / "daily_signal_replay.jsonl", daily_rows)
        _write_csv(out_dir / "daily_signal_replay.csv", daily_rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary
