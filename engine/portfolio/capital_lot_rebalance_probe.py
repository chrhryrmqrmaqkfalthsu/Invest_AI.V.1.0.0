"""Lot-level real-signal cap-binding rebalance probe.

Research-only module. It does not import or call live brokers, PositionManager, or
order code. The simulator keeps lots separate while enforcing ticker-level caps.

Key safeguards
--------------
* The probe never uses trades.jsonl wholesale. It first loads the selected
  deployable stage2 rulebook set from selected.jsonl and filters trades by
  (member_hash, rulebook_hash).
* Lot identity is canonicalized with immutable entry fields only:
  ticker/member_hash/rulebook_hash/entry_signal_date/entry_fill_date/sequence.
* Real daily replay rows are joined by (canonical_lot_id, decision_date). Price
  path proxy is retained only as a comparison metric and is not a fallback.
* Full probe execution is intended for after stage2 completion. While stage2 is
  running, use dry_run_plan() only.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from engine.portfolio.daily_signal_replay import (
    assign_canonical_lot_ids,
    canonical_lot_group_key,
)

STAGE2_DIR = Path("data/_system/research/honest_full_6174_20260610/stage2_batch_000")
DEFAULT_SELECTED_JSONL = STAGE2_DIR / "selected.jsonl"
DEFAULT_TRADES_JSONL = STAGE2_DIR / "trades.jsonl"
DEFAULT_DAILY_SIGNAL_JSONL = Path("data/_system/research/central_portfolio/daily_signal_replay/daily_signal_replay.jsonl")
DEFAULT_OHLCV_CACHE = Path("data/_system/research/honest_full_6174_20260610/stage0/ohlcv_cache")
OUT_DIR = Path("data/_system/research/central_portfolio/capital_lot_rebalance_probe")

MIN_EFFECT_PCT = 1.50
MAX_TICKER_SHARE_PCT = 30.0
MAX_ENTRY_SHARE_PCT = 20.0
MIN_JOIN_SUCCESS_RATE_PCT = 99.9
SIGNAL_VALID_NORMAL_PCT = 95.0
SIGNAL_VALID_WARN_PCT = 90.0
REQUIRED_TRADE_FIELDS = {
    "ticker",
    "member_hash",
    "rulebook_hash",
    "entry_date",
    "entry_signal_date",
    "entry_fill_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "entry_signal_score",
    "entry_signal_threshold",
    "exit_reason",
}
TIME_OUT_REASONS = {"time_out", "timeout"}
ORIGINAL_EXIT_REASONS = {"stop_loss", "sell_omen", "trailing", "take_profit", "time_out", "timeout", "breakeven_stop"}


@dataclass(frozen=True)
class LotProbeConfig:
    target_exposure_pct: float = 100.0
    cash_buffer_pct: float = 0.0
    max_entry_share_pct: float = MAX_ENTRY_SHARE_PCT
    max_ticker_share_pct: float = MAX_TICKER_SHARE_PCT
    slippage_bps: float = 5.0
    min_rebalance_days: int = 5
    weak_drop_pct: float = 15.0
    stronger_gap_pct: float = 20.0
    signal_to_weight_mode: str = "linear"
    lot_mode: str = "lot"
    use_real_signal: bool = True


@dataclass
class LotState:
    lot_id: str
    ticker: str
    entry_date: pd.Timestamp
    entry_signal_date: pd.Timestamp
    entry_fill_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    entry_signal_score: float
    entry_signal_threshold: float
    entry_strength: float
    weight: float
    last_price: float
    original_exit_reason: str = ""
    actual_exit_reason: str = "open"
    source_trade: Optional[dict[str, Any]] = None


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


def _date(value: Any) -> pd.Timestamp:
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


def _stage2_rows_with_line_no(path: Path, limit: Optional[int] = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not Path(path).exists():
        return out
    seen = 0
    with Path(path).open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            seen += 1
            row = json.loads(line)
            row["stage2_trade_line_no"] = line_no
            out.append(row)
            if limit and seen >= limit:
                break
    return out


def load_selected_rulebook_set(path: Path = DEFAULT_SELECTED_JSONL) -> dict[str, Any]:
    """Load selected/deployable rulebook pairs from stage2 selected.jsonl.

    Fails closed: no guessing from topn/trades is allowed when selected.jsonl is
    missing or malformed.
    """
    rows = _read_jsonl(Path(path))
    if not rows:
        raise RuntimeError(f"selected stage2 artifact missing or empty: {path}")
    selected_pairs: set[tuple[str, str]] = set()
    per_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    malformed = 0
    for row in rows:
        selected = row.get("selected") or {}
        ticker = str(row.get("ticker") or "").upper().strip()
        member_hash = str(selected.get("member_hash") or "").strip()
        rulebook_hash = str(selected.get("rulebook_hash") or "").strip()
        if not ticker or not member_hash or not rulebook_hash:
            malformed += 1
            continue
        pair = (member_hash, rulebook_hash)
        selected_pairs.add(pair)
        per_ticker[ticker].append({
            "ticker": ticker,
            "fold_label": row.get("fold_label") or row.get("label"),
            "run_key": row.get("run_key"),
            "member_hash": member_hash,
            "rulebook_hash": rulebook_hash,
            "rank": selected.get("rank"),
            "selection_rule_id": row.get("selection_rule_id") or selected.get("selection_rule_id"),
        })
    if malformed:
        raise RuntimeError(f"selected artifact has malformed rows: {malformed}")
    if not selected_pairs:
        raise RuntimeError(f"selected artifact did not identify deployable rulebooks: {path}")
    dist = Counter(len(v) for v in per_ticker.values())
    return {
        "path": str(path),
        "rows": len(rows),
        "ticker_count": len(per_ticker),
        "selected_pair_count": len(selected_pairs),
        "pairs": selected_pairs,
        "per_ticker_rulebook_count_dist": dict(dist),
        "per_ticker": per_ticker,
    }


def _valid_trade(row: dict[str, Any]) -> bool:
    if not REQUIRED_TRADE_FIELDS.issubset(row):
        return False
    ticker = str(row.get("ticker") or "").strip()
    if not ticker:
        return False
    score = _to_float(row.get("entry_signal_score"), -1.0)
    threshold = _to_float(row.get("entry_signal_threshold"), 0.0)
    if threshold <= 0 or score < threshold:
        return False
    if _to_float(row.get("entry_price"), 0.0) <= 0:
        return False
    if _date(row.get("exit_date")) <= _date(row.get("entry_fill_date") or row.get("entry_date")):
        return False
    return True


def _normalize_selected_trade(row: dict[str, Any]) -> dict[str, Any]:
    r = dict(row)
    r["ticker"] = str(r.get("ticker") or "").upper().strip()
    r["entry_date"] = str(r.get("entry_date") or r.get("entry_fill_date"))[:10]
    r["entry_fill_date"] = str(r.get("entry_fill_date") or r.get("entry_date"))[:10]
    r["entry_signal_date"] = str(r.get("entry_signal_date") or "")[:10]
    r["exit_date"] = str(r.get("exit_date") or "")[:10]
    return r


def load_stage2_trade_candidates(
    trades_jsonl: Path = DEFAULT_TRADES_JSONL,
    *,
    selected_jsonl: Path = DEFAULT_SELECTED_JSONL,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    selected = load_selected_rulebook_set(selected_jsonl)
    selected_pairs: set[tuple[str, str]] = selected["pairs"]
    raw_rows = _stage2_rows_with_line_no(Path(trades_jsonl), limit=limit)
    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        key = (str(row.get("member_hash") or "").strip(), str(row.get("rulebook_hash") or "").strip())
        if key not in selected_pairs:
            continue
        if not _valid_trade(row):
            continue
        rows.append(_normalize_selected_trade(row))
    return assign_canonical_lot_ids(rows)


def _daily_axis(rows: list[dict[str, Any]]) -> list[pd.Timestamp]:
    if not rows:
        return []
    start = min(_date(row["entry_signal_date"]) for row in rows)
    end = max(_date(row["exit_date"]) for row in rows)
    return list(pd.date_range(start, end, freq="B"))


def _entry_strength(row_or_lot: dict[str, Any] | LotState) -> float:
    if isinstance(row_or_lot, LotState):
        score = row_or_lot.entry_signal_score
        threshold = row_or_lot.entry_signal_threshold
    else:
        score = _to_float(row_or_lot.get("entry_signal_score"), 0.0)
        threshold = _to_float(row_or_lot.get("entry_signal_threshold"), 0.0)
    if threshold <= 0:
        return 1.0
    return max(0.0, score / threshold)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def signal_to_entry_weight(strength: float, config: LotProbeConfig) -> float:
    cap = max(0.0, config.max_entry_share_pct / 100.0)
    s = max(0.0, float(strength))
    mode = str(config.signal_to_weight_mode or "linear")
    if mode == "bucket":
        if s < 1.05:
            raw = 0.05
        elif s < 1.15:
            raw = 0.10
        elif s < 1.30:
            raw = 0.15
        else:
            raw = 0.20
    elif mode == "aggressive_linear":
        raw = 0.05 + 0.30 * _clamp(s - 1.0, 0.0, 1.0)
    else:
        raw = 0.05 + 0.20 * _clamp(s - 1.0, 0.0, 1.0)
    return min(cap, max(0.0, raw))


def _active_tickers(rows: list[dict[str, Any]], day: pd.Timestamp) -> set[str]:
    active: set[str] = set()
    for row in rows:
        if _date(row["entry_fill_date"]) <= day < _date(row["exit_date"]):
            active.add(str(row.get("ticker") or "").upper().strip())
    return active


def _load_ohlcv_cache(path: Path, tickers: Iterable[str]) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    for ticker in sorted({str(t).upper().strip() for t in tickers if str(t).strip()}):
        candidates = [Path(path) / f"{ticker}.pkl", Path(path) / f"{ticker}.parquet", Path(path) / f"{ticker}.csv"]
        df = None
        for candidate in candidates:
            if not candidate.exists():
                continue
            if candidate.suffix == ".pkl":
                df = pd.read_pickle(candidate)
            elif candidate.suffix == ".parquet":
                df = pd.read_parquet(candidate)
            else:
                df = pd.read_csv(candidate)
            break
        if df is None or df.empty:
            continue
        df = df.copy()
        if "Date" in df.columns:
            df["_date"] = pd.to_datetime(df["Date"]).dt.normalize()
            df = df.set_index("_date")
        elif "date" in df.columns:
            df["_date"] = pd.to_datetime(df["date"]).dt.normalize()
            df = df.set_index("_date")
        else:
            df.index = pd.to_datetime(df.index).normalize()
        rename = {}
        for col in df.columns:
            low = str(col).lower()
            if low == "close":
                rename[col] = "Close"
            elif low == "open":
                rename[col] = "Open"
            elif low == "high":
                rename[col] = "High"
            elif low == "low":
                rename[col] = "Low"
        df = df.rename(columns=rename)
        if "Close" in df.columns:
            histories[ticker] = df.sort_index()
    return histories


def _price(histories: dict[str, pd.DataFrame], ticker: str, day: pd.Timestamp, fallback: float, field: str = "Close") -> float:
    df = histories.get(str(ticker).upper().strip())
    if df is not None and not df.empty:
        d = pd.Timestamp(day).normalize()
        use_field = field if field in df.columns else "Close"
        if d in df.index and use_field in df.columns:
            return _to_float(df.loc[d][use_field], fallback)
        prev = df[df.index <= d]
        if not prev.empty and use_field in prev.columns:
            return _to_float(prev.iloc[-1][use_field], fallback)
    return float(fallback)


def _ticker_weight(open_lots: list[LotState], ticker: str) -> float:
    t = str(ticker).upper().strip()
    return sum(lot.weight for lot in open_lots if lot.ticker == t)


def _total_weight(open_lots: list[LotState]) -> float:
    return sum(max(0.0, lot.weight) for lot in open_lots)


def _mark_lots(open_lots: list[LotState], histories: dict[str, pd.DataFrame], day: pd.Timestamp, next_day: pd.Timestamp) -> float:
    portfolio_return = 0.0
    for lot in open_lots:
        p0 = _price(histories, lot.ticker, day, lot.last_price or lot.entry_price, field="Close")
        p1 = _price(histories, lot.ticker, next_day, p0, field="Close")
        if p0 > 0:
            portfolio_return += lot.weight * (p1 / p0 - 1.0)
        lot.last_price = p1
    return portfolio_return


def _sell_lot(open_lots: list[LotState], lot: LotState, slippage_bps: float, actual_reason: str) -> tuple[float, float]:
    if lot not in open_lots:
        return 0.0, 0.0
    open_lots.remove(lot)
    lot.actual_exit_reason = actual_reason
    turnover = lot.weight
    cost = turnover * max(0.0, slippage_bps) / 10000.0
    return lot.weight, cost


def _buy_lot(open_lots: list[LotState], row: dict[str, Any], weight: float, slippage_bps: float) -> tuple[LotState, float]:
    strength = _entry_strength(row)
    lot = LotState(
        lot_id=str(row.get("canonical_lot_id") or row.get("lot_id") or row.get("ticker")),
        ticker=str(row.get("ticker") or "").upper().strip(),
        entry_date=_date(row["entry_date"]),
        entry_signal_date=_date(row["entry_signal_date"]),
        entry_fill_date=_date(row["entry_fill_date"]),
        exit_date=_date(row["exit_date"]),
        entry_price=_to_float(row.get("entry_price")),
        exit_price=_to_float(row.get("exit_price"), _to_float(row.get("entry_price"))),
        entry_signal_score=_to_float(row.get("entry_signal_score")),
        entry_signal_threshold=_to_float(row.get("entry_signal_threshold")),
        entry_strength=strength,
        weight=max(0.0, float(weight)),
        last_price=_to_float(row.get("entry_price")),
        original_exit_reason=str(row.get("exit_reason") or ""),
        actual_exit_reason="open",
        source_trade=row,
    )
    open_lots.append(lot)
    cost = lot.weight * max(0.0, slippage_bps) / 10000.0
    return lot, cost


def _previous_trading_day(days: list[pd.Timestamp], idx: int) -> Optional[pd.Timestamp]:
    if idx <= 0:
        return None
    return days[idx - 1]


def load_daily_signal_lookup(path: Path = DEFAULT_DAILY_SIGNAL_JSONL) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    if not Path(path).exists():
        return lookup
    for row in _read_jsonl(Path(path)):
        lot_id = str(row.get("canonical_lot_id") or row.get("lot_id") or "").strip()
        decision_date = str(row.get("decision_date") or "")[:10]
        if not lot_id or not decision_date:
            continue
        # Only stage2 parity rows may drive decisions.
        if bool(row.get("use_llm_events", False)):
            continue
        lookup[(lot_id, decision_date)] = row
    return lookup


def _signal_row(signal_lookup: dict[tuple[str, str], dict[str, Any]], lot_id: str, decision_date: pd.Timestamp | str) -> Optional[dict[str, Any]]:
    return signal_lookup.get((str(lot_id), str(pd.Timestamp(decision_date).date())))


def _sellable_rebalance_lots(
    open_lots: list[LotState],
    signal_lookup: dict[tuple[str, str], dict[str, Any]],
    decision_date: pd.Timestamp,
    rebalance_fill_date: pd.Timestamp,
    new_strength: float,
    config: LotProbeConfig,
) -> list[LotState]:
    out: list[tuple[float, LotState]] = []
    weak_drop = max(0.0, config.weak_drop_pct)
    gap = max(0.0, config.stronger_gap_pct) / 100.0
    for lot in open_lots:
        if lot.exit_date <= rebalance_fill_date:
            continue
        if lot.actual_exit_reason != "open":
            continue
        sig = _signal_row(signal_lookup, lot.lot_id, decision_date)
        if not sig or not bool(sig.get("signal_valid", False)):
            continue
        decay = _to_float(sig.get("strength_decay_pct"), 0.0)
        current_strength = _to_float(sig.get("current_strength"), lot.entry_strength)
        if decay < weak_drop:
            continue
        if new_strength < current_strength * (1.0 + gap):
            continue
        out.append((current_strength, lot))
    out.sort(key=lambda x: (x[0], x[1].ticker, x[1].lot_id))
    return [lot for _, lot in out]


def _summarize_signal_join(trades: list[dict[str, Any]], signal_lookup: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    lot_ids = {str(row.get("canonical_lot_id")) for row in trades}
    joined_lot_ids = {lot_id for lot_id, _date_key in signal_lookup if lot_id in lot_ids}
    by_year: dict[str, list[bool]] = defaultdict(list)
    by_ticker: dict[str, list[bool]] = defaultdict(list)
    by_decision_date: dict[str, list[bool]] = defaultdict(list)
    valid = 0
    total = 0
    for (lot_id, decision_date), row in signal_lookup.items():
        if lot_id not in lot_ids:
            continue
        is_valid = bool(row.get("signal_valid", False))
        total += 1
        valid += int(is_valid)
        ticker = str(row.get("ticker") or "")
        year = str(decision_date)[:4]
        by_year[year].append(is_valid)
        by_ticker[ticker].append(is_valid)
        by_decision_date[decision_date].append(is_valid)
    rate = valid / total * 100.0 if total else 0.0
    status = "normal" if rate >= SIGNAL_VALID_NORMAL_PCT else ("warning" if rate >= SIGNAL_VALID_WARN_PCT else "hold_interpretation")
    return {
        "lot_count": len(lot_ids),
        "joined_lot_count": len(joined_lot_ids),
        "join_success_rate_by_lot": len(joined_lot_ids) / max(1, len(lot_ids)) * 100.0,
        "signal_rows": total,
        "signal_valid_rows": valid,
        "signal_valid_rate_row": rate,
        "signal_valid_interpretation": status,
        "signal_valid_rate_by_year": {k: sum(v) / len(v) * 100.0 for k, v in sorted(by_year.items()) if v},
        "signal_valid_rate_by_ticker": {k: sum(v) / len(v) * 100.0 for k, v in sorted(by_ticker.items()) if v},
        "signal_valid_rate_by_decision_date": {k: sum(v) / len(v) * 100.0 for k, v in sorted(by_decision_date.items()) if v},
    }


def _check_fail_gates(trades: list[dict[str, Any]], signal_lookup: dict[tuple[str, str], dict[str, Any]], *, require_signals: bool) -> dict[str, Any]:
    duplicate = len(trades) - len({row.get("canonical_lot_id") for row in trades})
    join = _summarize_signal_join(trades, signal_lookup) if require_signals else {
        "join_success_rate_by_lot": 0.0,
        "signal_valid_rate_row": 0.0,
        "signal_valid_interpretation": "not_checked_no_daily_signal_required",
    }
    failed: list[str] = []
    if duplicate != 0:
        failed.append("duplicate_canonical_lot_id")
    if require_signals and join.get("join_success_rate_by_lot", 0.0) < MIN_JOIN_SUCCESS_RATE_PCT:
        failed.append("join_success_rate_by_lot")
    return {
        "duplicate_canonical_lot_id": duplicate,
        "missing_rulebook": 0,
        "entry_replay_fail": 0,
        "join": join,
        "failed_gates": failed,
        "passed": not failed,
    }


def simulate_lot_rebalance(
    trades: list[dict[str, Any]],
    histories: dict[str, pd.DataFrame],
    config: LotProbeConfig,
    *,
    signal_lookup: Optional[dict[tuple[str, str], dict[str, Any]]] = None,
) -> dict[str, Any]:
    signal_lookup = signal_lookup or {}
    rows = sorted([row for row in trades if _valid_trade(row)], key=lambda r: (_date(r["entry_fill_date"]), -_entry_strength(r), str(r.get("ticker")), str(r.get("canonical_lot_id"))))
    days = _daily_axis(rows)
    by_fill: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_fill[_date(row["entry_fill_date"])].append(row)
    open_lots: list[LotState] = []
    bought_lot_ids: set[str] = set()
    closed_lots: list[LotState] = []
    capital = 1.0
    cost_pct = 0.0
    turnover = 0.0
    buy_count = 0
    reallocation_sell_count = 0
    natural_exit_count = 0
    skipped_no_cash = 0
    skipped_ticker_cap = 0
    skipped_missing_signal = 0
    gross_exposure_sum = 0.0
    active_days = 0
    full_exposure_days = 0
    max_ticker_share = 0.0
    baseline_time_out_loss = 0.0
    original_timeout_total_loss = 0.0
    actual_timeout_loss = 0.0
    rebalance_sell_from_original_timeout_pnl = 0.0
    lot_return_units: dict[str, float] = defaultdict(float)
    kill_modeled = False

    target_exposure = max(0.0, min(1.0, (config.target_exposure_pct - config.cash_buffer_pct) / 100.0))
    ticker_cap = max(0.0, config.max_ticker_share_pct / 100.0)
    for idx, day in enumerate(days[:-1]):
        decision_date = _previous_trading_day(days, idx)
        # Natural exits and original exit policy are always before rebalance.
        still_open: list[LotState] = []
        for lot in open_lots:
            if lot.exit_date <= day:
                natural_exit_count += 1
                turnover += lot.weight
                cost = lot.weight * max(0.0, config.slippage_bps) / 10000.0
                cost_pct += cost
                capital *= max(0.0, 1.0 - cost)
                lot.actual_exit_reason = lot.original_exit_reason or "original_exit"
                if lot.original_exit_reason in TIME_OUT_REASONS:
                    pnl = lot_return_units.get(lot.lot_id, 0.0)
                    if pnl < 0:
                        baseline_time_out_loss += pnl
                        original_timeout_total_loss += pnl
                        actual_timeout_loss += pnl
                closed_lots.append(lot)
            else:
                still_open.append(lot)
        open_lots = still_open

        for row in by_fill.get(day, []):
            lot_id = str(row.get("canonical_lot_id"))
            if lot_id in bought_lot_ids:
                continue
            # Candidate is only available when stage2 says decision T and fill T+1.
            if _date(row["entry_fill_date"]) != day:
                continue
            if decision_date is not None and _date(row["entry_signal_date"]) != decision_date:
                continue
            if decision_date is not None:
                sig = _signal_row(signal_lookup, lot_id, decision_date)
                if sig and sig.get("current_strength") is not None and bool(sig.get("signal_valid", False)):
                    strength = _to_float(sig.get("current_strength"), _entry_strength(row))
                else:
                    # Entry score is logged stage2 truth, not a price proxy fallback.
                    strength = _entry_strength(row)
            else:
                strength = _entry_strength(row)
            desired = signal_to_entry_weight(strength, config)
            desired = min(desired, max(0.0, target_exposure - _total_weight(open_lots)))
            ticker = str(row.get("ticker") or "").upper().strip()
            ticker_room = max(0.0, ticker_cap - _ticker_weight(open_lots, ticker))
            desired = min(desired, ticker_room)
            if desired <= 1e-12 and decision_date is not None and config.use_real_signal:
                for weak_lot in _sellable_rebalance_lots(open_lots, signal_lookup, decision_date, day, strength, config):
                    freed, sell_cost = _sell_lot(open_lots, weak_lot, config.slippage_bps, actual_reason="rebalance_sell")
                    if freed <= 0:
                        continue
                    turnover += freed
                    cost_pct += sell_cost
                    capital *= max(0.0, 1.0 - sell_cost)
                    reallocation_sell_count += 1
                    if weak_lot.original_exit_reason in TIME_OUT_REASONS:
                        pnl = lot_return_units.get(weak_lot.lot_id, 0.0)
                        rebalance_sell_from_original_timeout_pnl += pnl
                        if pnl < 0:
                            original_timeout_total_loss += pnl
                    closed_lots.append(weak_lot)
                    ticker_room = max(0.0, ticker_cap - _ticker_weight(open_lots, ticker))
                    desired = min(signal_to_entry_weight(strength, config), freed, ticker_room, max(0.0, target_exposure - _total_weight(open_lots)))
                    if desired > 1e-12:
                        break
            if desired <= 1e-12:
                if ticker_room <= 1e-12:
                    skipped_ticker_cap += 1
                elif decision_date is not None and config.use_real_signal and not _signal_row(signal_lookup, lot_id, decision_date):
                    skipped_missing_signal += 1
                else:
                    skipped_no_cash += 1
                continue
            _lot, buy_cost = _buy_lot(open_lots, row, desired, config.slippage_bps)
            bought_lot_ids.add(lot_id)
            turnover += desired
            cost_pct += buy_cost
            capital *= max(0.0, 1.0 - buy_cost)
            buy_count += 1

        gross = _total_weight(open_lots)
        gross_exposure_sum += gross
        if gross > 0:
            active_days += 1
        if gross >= target_exposure - 1e-9:
            full_exposure_days += 1
        ticker_weights: dict[str, float] = {}
        for lot in open_lots:
            ticker_weights[lot.ticker] = ticker_weights.get(lot.ticker, 0.0) + lot.weight
        max_ticker_share = max(max_ticker_share, max(ticker_weights.values(), default=0.0) * 100.0)

        next_day = days[idx + 1]
        daily_ret = _mark_lots(open_lots, histories, day, next_day)
        for lot in open_lots:
            p0 = _price(histories, lot.ticker, day, lot.last_price or lot.entry_price, field="Close")
            p1 = _price(histories, lot.ticker, next_day, p0, field="Close")
            if p0 > 0:
                lot_return_units[lot.lot_id] += capital * lot.weight * (p1 / p0 - 1.0)
        capital *= 1.0 + daily_ret

    total_return_net_pct = (capital - 1.0) * 100.0
    signal_join = _summarize_signal_join(rows, signal_lookup) if config.use_real_signal else {}
    return {
        "total_return_net_pct": total_return_net_pct,
        "ending_capital_multiple": capital,
        "slippage_cost_pct_initial_capital": cost_pct * 100.0,
        "turnover_sum": turnover,
        "buy_count": buy_count,
        "natural_exit_count": natural_exit_count,
        "reallocation_sell_count": reallocation_sell_count,
        "skipped_no_cash": skipped_no_cash,
        "skipped_ticker_cap": skipped_ticker_cap,
        "skipped_missing_signal": skipped_missing_signal,
        "avg_gross_exposure_pct": gross_exposure_sum / max(1, len(days) - 1) * 100.0,
        "full_exposure_day_pct": full_exposure_days / max(1, len(days) - 1) * 100.0,
        "active_day_count": active_days,
        "max_ticker_gross_share_pct": max_ticker_share,
        "baseline_time_out_loss_units": baseline_time_out_loss,
        "original_time_out_lot_total_loss_units": original_timeout_total_loss,
        "actual_time_out_loss_units": actual_timeout_loss,
        "rebalance_sell_from_original_time_out_pnl_units": rebalance_sell_from_original_timeout_pnl,
        "time_out_loss_pnl_units": original_timeout_total_loss,
        "kill_modeled": kill_modeled,
        "trade_count": len(rows),
        "ticker_count": len({row["ticker"] for row in rows}),
        "signal_join": signal_join,
        "config": config.__dict__,
    }


def dry_run_plan(
    trades_jsonl: Path = DEFAULT_TRADES_JSONL,
    ohlcv_cache: Path = DEFAULT_OHLCV_CACHE,
    *,
    selected_jsonl: Path = DEFAULT_SELECTED_JSONL,
    daily_signal_jsonl: Path = DEFAULT_DAILY_SIGNAL_JSONL,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    selected = load_selected_rulebook_set(selected_jsonl)
    raw_rows = _stage2_rows_with_line_no(Path(trades_jsonl), limit=limit)
    rows = load_stage2_trade_candidates(trades_jsonl, selected_jsonl=selected_jsonl, limit=limit)
    duplicate = len(rows) - len({row.get("canonical_lot_id") for row in rows})
    days = _daily_axis(rows)
    active_counts = [len(_active_tickers(rows, day)) for day in days] if days else []
    min_required_tickers = int((100 + MAX_TICKER_SHARE_PCT - 1) // MAX_TICKER_SHARE_PCT)
    feasible_days = sum(1 for n in active_counts if n * MAX_TICKER_SHARE_PCT >= 100.0)
    tickers = sorted({str(row.get("ticker") or "").upper().strip() for row in rows})
    pkl_available = sum(1 for ticker in tickers if (Path(ohlcv_cache) / f"{ticker}.pkl").exists())
    signal_lookup = load_daily_signal_lookup(daily_signal_jsonl)
    gates = _check_fail_gates(rows, signal_lookup, require_signals=Path(daily_signal_jsonl).exists())
    return {
        "gate": "capital_lot_rebalance_probe_dry_plan",
        "stage2_running_guard": "full_probe_not_executed_when_stage2_running",
        "selected_artifact": str(selected_jsonl),
        "selected_rows": selected["rows"],
        "selected_ticker_count": selected["ticker_count"],
        "selected_pair_count": selected["selected_pair_count"],
        "selected_per_ticker_rulebook_count_dist": selected["per_ticker_rulebook_count_dist"],
        "trades_jsonl": str(trades_jsonl),
        "raw_trade_rows_read": len(raw_rows),
        "selected_trade_rows": len(rows),
        "daily_signal_jsonl": str(daily_signal_jsonl),
        "daily_signal_exists": Path(daily_signal_jsonl).exists(),
        "daily_signal_rows_loaded": len(signal_lookup),
        "duplicate_canonical_lot_id": duplicate,
        "fail_gates": gates,
        "ticker_count": len(tickers),
        "date_min": str(days[0].date()) if days else None,
        "date_max": str(days[-1].date()) if days else None,
        "trading_days": len(days),
        "has_daily_recomputed_signal": Path(daily_signal_jsonl).exists(),
        "real_signal_required_for_rebalance": True,
        "proxy_fallback_forbidden": True,
        "kill_modeled": False,
        "kill_modeled_reason": "no kill/forced liquidation artifact wired; not invented",
        "execution_timing": "decision_date T close -> T+1 open fill; sell before buy on fill day",
        "min_tickers_for_100pct_with_30pct_cap": min_required_tickers,
        "active_ticker_count_min": min(active_counts) if active_counts else 0,
        "active_ticker_count_avg": sum(active_counts) / len(active_counts) if active_counts else 0.0,
        "active_ticker_count_max": max(active_counts) if active_counts else 0,
        "cap_100pct_feasible_days": feasible_days,
        "cap_100pct_feasible_day_pct": feasible_days / len(active_counts) * 100.0 if active_counts else 0.0,
        "ohlcv_pkl_available_for_selected_tickers": pkl_available,
        "parameter_grid": {
            "weak_drop_pct": [10.0, 15.0, 25.0],
            "stronger_gap_pct": [10.0, 20.0, 30.0],
            "signal_to_weight_mode": ["linear", "bucket", "aggressive_linear"],
            "slippage_bps": [0.0, 5.0],
            "lot_mode": ["lot"],
        },
        "will_not_execute_heavy_backtest_in_dry_run": True,
    }


def _config_grid(slippage_bps: float) -> list[LotProbeConfig]:
    configs: list[LotProbeConfig] = []
    for weak in [10.0, 15.0, 25.0]:
        for gap in [10.0, 20.0, 30.0]:
            for mode in ["linear", "bucket", "aggressive_linear"]:
                configs.append(LotProbeConfig(weak_drop_pct=weak, stronger_gap_pct=gap, signal_to_weight_mode=mode, slippage_bps=slippage_bps, lot_mode="lot", use_real_signal=True))
    return configs


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)


def run_capital_lot_rebalance_probe(
    *,
    trades_jsonl: Path = DEFAULT_TRADES_JSONL,
    selected_jsonl: Path = DEFAULT_SELECTED_JSONL,
    daily_signal_jsonl: Path = DEFAULT_DAILY_SIGNAL_JSONL,
    ohlcv_cache: Path = DEFAULT_OHLCV_CACHE,
    out_dir: Path = OUT_DIR,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    trades = load_stage2_trade_candidates(trades_jsonl, selected_jsonl=selected_jsonl, limit=limit)
    if not trades:
        raise ValueError(f"no valid selected stage2 trades: {trades_jsonl}")
    signal_lookup = load_daily_signal_lookup(daily_signal_jsonl)
    gates = _check_fail_gates(trades, signal_lookup, require_signals=True)
    if not gates["passed"]:
        raise RuntimeError(f"fail gates did not pass: {gates}")
    histories = _load_ohlcv_cache(ohlcv_cache, [row["ticker"] for row in trades])
    if not histories:
        raise ValueError(f"no histories loaded from {ohlcv_cache}")

    baseline_config = LotProbeConfig(signal_to_weight_mode="bucket", weak_drop_pct=999.0, stronger_gap_pct=999.0, slippage_bps=5.0, use_real_signal=True)
    baseline_net = simulate_lot_rebalance(trades, histories, baseline_config, signal_lookup=signal_lookup)
    baseline_zero = simulate_lot_rebalance(trades, histories, LotProbeConfig(**{**baseline_config.__dict__, "slippage_bps": 0.0}), signal_lookup=signal_lookup)

    result_rows: list[dict[str, Any]] = []
    best: Optional[dict[str, Any]] = None
    for slip in [0.0, 5.0]:
        for config in _config_grid(slippage_bps=slip):
            metrics = simulate_lot_rebalance(trades, histories, config, signal_lookup=signal_lookup)
            base = baseline_zero if slip == 0.0 else baseline_net
            delta = metrics["total_return_net_pct"] - base["total_return_net_pct"]
            time_out_not_worse = metrics["original_time_out_lot_total_loss_units"] >= base["original_time_out_lot_total_loss_units"] - 1e-12 and metrics["actual_time_out_loss_units"] >= base["actual_time_out_loss_units"] - 1e-12
            row = {
                "weak_drop_pct": config.weak_drop_pct,
                "stronger_gap_pct": config.stronger_gap_pct,
                "signal_to_weight_mode": config.signal_to_weight_mode,
                "slippage_bps": config.slippage_bps,
                "return_delta_pct": delta,
                "total_return_net_pct": metrics["total_return_net_pct"],
                "baseline_return_pct": base["total_return_net_pct"],
                "avg_gross_exposure_pct": metrics["avg_gross_exposure_pct"],
                "full_exposure_day_pct": metrics["full_exposure_day_pct"],
                "max_ticker_gross_share_pct": metrics["max_ticker_gross_share_pct"],
                "turnover_sum": metrics["turnover_sum"],
                "slippage_cost_pct_initial_capital": metrics["slippage_cost_pct_initial_capital"],
                "buy_count": metrics["buy_count"],
                "reallocation_sell_count": metrics["reallocation_sell_count"],
                "skipped_no_cash": metrics["skipped_no_cash"],
                "skipped_ticker_cap": metrics["skipped_ticker_cap"],
                "skipped_missing_signal": metrics["skipped_missing_signal"],
                "original_time_out_lot_total_loss_units": metrics["original_time_out_lot_total_loss_units"],
                "actual_time_out_loss_units": metrics["actual_time_out_loss_units"],
                "rebalance_sell_from_original_time_out_pnl_units": metrics["rebalance_sell_from_original_time_out_pnl_units"],
                "time_out_not_worse": time_out_not_worse,
                "passed_min_effect_after_slippage": bool(slip == 5.0 and delta >= MIN_EFFECT_PCT and time_out_not_worse and metrics["max_ticker_gross_share_pct"] <= MAX_TICKER_SHARE_PCT + 1e-9),
            }
            result_rows.append(row)
            if slip == 5.0 and (best is None or row["return_delta_pct"] > best["return_delta_pct"]):
                best = row

    selected = load_selected_rulebook_set(selected_jsonl)
    summary = {
        "gate": "capital_lot_rebalance_probe",
        "selected_artifact": str(selected_jsonl),
        "selected_ticker_count": selected["ticker_count"],
        "selected_pair_count": selected["selected_pair_count"],
        "stage2_source": str(trades_jsonl),
        "daily_signal_source": str(daily_signal_jsonl),
        "ohlcv_cache": str(ohlcv_cache),
        "out_dir": str(out_dir),
        "trade_count": len(trades),
        "ticker_count": len({row["ticker"] for row in trades}),
        "fail_gates": gates,
        "baseline_fixed_lot_slippage0": baseline_zero,
        "baseline_fixed_lot_slippage5": baseline_net,
        "candidate_count": len(result_rows),
        "best_after_5bps_slippage": best,
        "implementation_recommended_prelim": bool(best and best.get("passed_min_effect_after_slippage")),
        "criteria_note": "Full acceptance still requires year-by-year and leave-one-ticker-out checks after stage2 completion.",
        "results": result_rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "summary.json", summary)
    _write_csv(out_dir / "probe_results.csv", result_rows)
    return summary
