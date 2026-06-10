"""Lot-level signal-proportional entry and cap-binding rebalance probe.

Research-only module. It does not import or call live brokers, PositionManager, or
order code. The simulator keeps lots separate even when the ticker is identical,
while enforcing ticker-level aggregate caps.

The full probe is intentionally designed for post-stage2 execution. During stage2
runs, use dry_run_plan() only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

STAGE2_DIR = Path("data/_system/research/honest_full_6174_20260610/stage2_batch_000")
DEFAULT_TRADES_JSONL = STAGE2_DIR / "trades.jsonl"
DEFAULT_RULEBOOKS_JSONL = STAGE2_DIR / "topn_rulebooks.jsonl"
DEFAULT_OHLCV_CACHE = Path("data/_system/research/honest_full_6174_20260610/stage0/ohlcv_cache")
OUT_DIR = Path("data/_system/research/central_portfolio/capital_lot_rebalance_probe")

MIN_EFFECT_PCT = 1.50
MAX_TICKER_SHARE_PCT = 30.0
MAX_ENTRY_SHARE_PCT = 20.0
REQUIRED_TRADE_FIELDS = {
    "ticker",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "entry_signal_score",
    "entry_signal_threshold",
    "exit_reason",
}
TIME_OUT_REASONS = {"time_out", "timeout"}


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
    current_signal_mode: str = "price_path_proxy"
    lot_mode: str = "lot"


@dataclass
class LotState:
    lot_id: str
    ticker: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    entry_signal_score: float
    entry_signal_threshold: float
    weight: float
    last_price: float
    max_unrealized_return: float = 0.0
    exit_reason: str = ""
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
    return pd.Timestamp(str(value)[:10])


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


def _trade_id(row: dict[str, Any], index: int) -> str:
    ticker = str(row.get("ticker") or "").upper().strip()
    member = str(row.get("member_hash") or row.get("rulebook_hash") or "")[:12]
    return f"{ticker}:{member}:{row.get('entry_date')}:{row.get('exit_date')}:{index}"


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
    if _date(row.get("exit_date")) <= _date(row.get("entry_date")):
        return False
    return True


def load_stage2_trade_candidates(path: Path = DEFAULT_TRADES_JSONL, limit: Optional[int] = None) -> list[dict[str, Any]]:
    rows = _read_jsonl(Path(path), limit=limit)
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        if not _valid_trade(row):
            continue
        r = dict(row)
        r["_lot_id"] = _trade_id(r, i)
        r["ticker"] = str(r.get("ticker") or "").upper().strip()
        out.append(r)
    return out


def _daily_axis(rows: list[dict[str, Any]]) -> list[pd.Timestamp]:
    if not rows:
        return []
    start = min(_date(row["entry_date"]) for row in rows)
    end = max(_date(row["exit_date"]) for row in rows)
    return list(pd.date_range(start, end, freq="B"))


def _live_strength(row_or_lot: dict[str, Any] | LotState) -> float:
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
    """Convert signal strength to target lot weight, capped at 20% by default."""
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
    else:  # linear
        raw = 0.05 + 0.20 * _clamp(s - 1.0, 0.0, 1.0)
    return min(cap, max(0.0, raw))


def _active_tickers(rows: list[dict[str, Any]], day: pd.Timestamp) -> set[str]:
    active: set[str] = set()
    for row in rows:
        if _date(row["entry_date"]) <= day < _date(row["exit_date"]):
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
        # Normalize likely lowercase columns.
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


def _price(histories: dict[str, pd.DataFrame], ticker: str, day: pd.Timestamp, fallback: float) -> float:
    df = histories.get(str(ticker).upper().strip())
    if df is not None and not df.empty:
        d = pd.Timestamp(day).normalize()
        if d in df.index and "Close" in df.columns:
            return _to_float(df.loc[d]["Close"], fallback)
        prev = df[df.index <= d]
        if not prev.empty and "Close" in prev.columns:
            return _to_float(prev.iloc[-1]["Close"], fallback)
    return float(fallback)


def _lot_unrealized(lot: LotState, histories: dict[str, pd.DataFrame], day: pd.Timestamp) -> float:
    p = _price(histories, lot.ticker, day, lot.last_price or lot.entry_price)
    if lot.entry_price <= 0:
        return 0.0
    return p / lot.entry_price - 1.0


def current_signal_proxy(lot: LotState, histories: dict[str, pd.DataFrame], day: pd.Timestamp, config: LotProbeConfig) -> float:
    """Approximate current signal for dry/probe when full daily signal recomputation is absent.

    The stage2 trade logs contain entry signal, but not a daily recomputed signal.
    Full execution therefore labels this as a price-path proxy unless a later
    signal-provider is added.
    """
    if config.current_signal_mode == "entry_constant":
        return lot.entry_signal_score
    ret = _lot_unrealized(lot, histories, day)
    lot.max_unrealized_return = max(lot.max_unrealized_return, ret)
    fade = max(0.0, lot.max_unrealized_return - ret)
    # Profitable path supports signal, fading path weakens it.
    return max(0.0, lot.entry_signal_score * (1.0 + 0.50 * ret - 1.50 * fade))


def _ticker_weight(open_lots: list[LotState], ticker: str) -> float:
    t = str(ticker).upper().strip()
    return sum(lot.weight for lot in open_lots if lot.ticker == t)


def _total_weight(open_lots: list[LotState]) -> float:
    return sum(max(0.0, lot.weight) for lot in open_lots)


def _mark_lots(open_lots: list[LotState], histories: dict[str, pd.DataFrame], day: pd.Timestamp, next_day: pd.Timestamp) -> float:
    portfolio_return = 0.0
    for lot in open_lots:
        p0 = _price(histories, lot.ticker, day, lot.last_price or lot.entry_price)
        p1 = _price(histories, lot.ticker, next_day, p0)
        if p0 > 0:
            portfolio_return += lot.weight * (p1 / p0 - 1.0)
        lot.last_price = p1
    return portfolio_return


def _sell_lot(open_lots: list[LotState], lot: LotState, slippage_bps: float) -> tuple[float, float]:
    if lot not in open_lots:
        return 0.0, 0.0
    open_lots.remove(lot)
    turnover = lot.weight
    cost = turnover * max(0.0, slippage_bps) / 10000.0
    return lot.weight, cost


def _buy_lot(open_lots: list[LotState], row: dict[str, Any], weight: float, slippage_bps: float) -> tuple[LotState, float]:
    lot = LotState(
        lot_id=str(row.get("_lot_id") or row.get("trade_id") or row.get("ticker")),
        ticker=str(row.get("ticker") or "").upper().strip(),
        entry_date=_date(row["entry_date"]),
        exit_date=_date(row["exit_date"]),
        entry_price=_to_float(row.get("entry_price")),
        exit_price=_to_float(row.get("exit_price"), _to_float(row.get("entry_price"))),
        entry_signal_score=_to_float(row.get("entry_signal_score")),
        entry_signal_threshold=_to_float(row.get("entry_signal_threshold")),
        weight=max(0.0, float(weight)),
        last_price=_to_float(row.get("entry_price")),
        exit_reason=str(row.get("exit_reason") or ""),
        source_trade=row,
    )
    open_lots.append(lot)
    cost = lot.weight * max(0.0, slippage_bps) / 10000.0
    return lot, cost


def _sellable_rebalance_lots(
    open_lots: list[LotState],
    histories: dict[str, pd.DataFrame],
    day: pd.Timestamp,
    new_strength: float,
    config: LotProbeConfig,
) -> list[LotState]:
    out: list[tuple[float, LotState]] = []
    weak_drop = max(0.0, config.weak_drop_pct) / 100.0
    gap = max(0.0, config.stronger_gap_pct) / 100.0
    for lot in open_lots:
        unrealized = _lot_unrealized(lot, histories, day)
        if unrealized < 0.0:
            continue  # 손실 중인 lot은 재배치 매도 대상 제외.
        current_signal = current_signal_proxy(lot, histories, day, config)
        if current_signal > lot.entry_signal_score * (1.0 - weak_drop):
            continue
        if new_strength * lot.entry_signal_threshold < current_signal * (1.0 + gap):
            continue
        # Smaller current signal and lower unrealized are sold first.
        out.append((current_signal + max(0.0, unrealized), lot))
    out.sort(key=lambda x: (x[0], x[1].ticker, x[1].lot_id))
    return [lot for _, lot in out]


def simulate_lot_rebalance(
    trades: list[dict[str, Any]],
    histories: dict[str, pd.DataFrame],
    config: LotProbeConfig,
) -> dict[str, Any]:
    rows = sorted([row for row in trades if _valid_trade(row)], key=lambda r: (_date(r["entry_date"]), -_live_strength(r), str(r.get("ticker"))))
    days = _daily_axis(rows)
    by_entry: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    for row in rows:
        by_entry.setdefault(_date(row["entry_date"]), []).append(row)
    open_lots: list[LotState] = []
    capital = 1.0
    cost_pct = 0.0
    turnover = 0.0
    buy_count = 0
    reallocation_sell_count = 0
    natural_exit_count = 0
    skipped_no_cash = 0
    skipped_ticker_cap = 0
    gross_exposure_sum = 0.0
    active_days = 0
    full_exposure_days = 0
    max_ticker_share = 0.0
    time_out_loss_units = 0.0
    lot_return_units: dict[str, float] = {}

    target_exposure = max(0.0, min(1.0, (config.target_exposure_pct - config.cash_buffer_pct) / 100.0))
    ticker_cap = max(0.0, config.max_ticker_share_pct / 100.0)
    for idx, day in enumerate(days[:-1]):
        # Natural lot exits first; existing stop/take/trailing logic is preserved by using original exit dates.
        still_open: list[LotState] = []
        for lot in open_lots:
            if lot.exit_date <= day:
                natural_exit_count += 1
                turnover += lot.weight
                cost = lot.weight * max(0.0, config.slippage_bps) / 10000.0
                cost_pct += cost
                capital *= max(0.0, 1.0 - cost)
                if lot.exit_reason in TIME_OUT_REASONS:
                    pnl = lot_return_units.get(lot.lot_id, 0.0)
                    if pnl < 0:
                        time_out_loss_units += pnl
            else:
                still_open.append(lot)
        open_lots = still_open

        for row in by_entry.get(day, []):
            ticker = str(row.get("ticker") or "").upper().strip()
            strength = _live_strength(row)
            desired = signal_to_entry_weight(strength, config)
            desired = min(desired, max(0.0, target_exposure - _total_weight(open_lots)))
            ticker_room = max(0.0, ticker_cap - _ticker_weight(open_lots, ticker))
            desired = min(desired, ticker_room)
            if desired <= 1e-12:
                # Try cap-binding reallocation: sell weak/profitable lots if the new candidate is clearly stronger.
                for weak_lot in _sellable_rebalance_lots(open_lots, histories, day, strength, config):
                    freed, sell_cost = _sell_lot(open_lots, weak_lot, config.slippage_bps)
                    if freed <= 0:
                        continue
                    turnover += freed
                    cost_pct += sell_cost
                    capital *= max(0.0, 1.0 - sell_cost)
                    reallocation_sell_count += 1
                    ticker_room = max(0.0, ticker_cap - _ticker_weight(open_lots, ticker))
                    desired = min(signal_to_entry_weight(strength, config), freed, ticker_room, max(0.0, target_exposure - _total_weight(open_lots)))
                    if desired > 1e-12:
                        break
            if desired <= 1e-12:
                if ticker_room <= 1e-12:
                    skipped_ticker_cap += 1
                else:
                    skipped_no_cash += 1
                continue
            lot, buy_cost = _buy_lot(open_lots, row, desired, config.slippage_bps)
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
            # Approximate contribution after daily mark for lot-level timeout loss accounting.
            p0 = _price(histories, lot.ticker, day, lot.last_price or lot.entry_price)
            p1 = _price(histories, lot.ticker, next_day, p0)
            if p0 > 0:
                lot_return_units[lot.lot_id] = lot_return_units.get(lot.lot_id, 0.0) + capital * lot.weight * (p1 / p0 - 1.0)
        capital *= 1.0 + daily_ret

    total_return_net_pct = (capital - 1.0) * 100.0
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
        "avg_gross_exposure_pct": gross_exposure_sum / max(1, len(days) - 1) * 100.0,
        "full_exposure_day_pct": full_exposure_days / max(1, len(days) - 1) * 100.0,
        "active_day_count": active_days,
        "max_ticker_gross_share_pct": max_ticker_share,
        "time_out_loss_pnl_units": time_out_loss_units,
        "trade_count": len(rows),
        "ticker_count": len({row["ticker"] for row in rows}),
        "config": config.__dict__,
    }


def _synthetic_histories(tickers: Iterable[str], dates: Iterable[pd.Timestamp]) -> dict[str, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    idx = pd.DatetimeIndex([pd.Timestamp(d).normalize() for d in dates])
    for i, ticker in enumerate(sorted(set(tickers))):
        prices = [100.0 * (1.0 + (j * (0.002 if i % 2 == 0 else -0.001))) for j in range(len(idx))]
        histories[ticker] = pd.DataFrame({"Close": prices}, index=idx)
    return histories


def dry_run_plan(
    trades_jsonl: Path = DEFAULT_TRADES_JSONL,
    ohlcv_cache: Path = DEFAULT_OHLCV_CACHE,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    raw_rows = _read_jsonl(Path(trades_jsonl), limit=limit)
    rows = [row for row in raw_rows if _valid_trade(row)]
    days = _daily_axis(rows)
    active_counts = [len(_active_tickers(rows, day)) for day in days] if days else []
    min_required_tickers = int((100 + MAX_TICKER_SHARE_PCT - 1) // MAX_TICKER_SHARE_PCT)
    feasible_days = sum(1 for n in active_counts if n * MAX_TICKER_SHARE_PCT >= 100.0)
    tickers = sorted({str(row.get("ticker") or "").upper().strip() for row in rows})
    pkl_available = sum(1 for ticker in tickers if (Path(ohlcv_cache) / f"{ticker}.pkl").exists())
    sample = rows[0] if rows else {}
    missing = sorted(REQUIRED_TRADE_FIELDS - set(sample)) if sample else sorted(REQUIRED_TRADE_FIELDS)
    return {
        "gate": "capital_lot_rebalance_probe_dry_plan",
        "stage2_running_guard": "full_probe_not_executed_when_stage2_running",
        "trades_jsonl": str(trades_jsonl),
        "ohlcv_cache": str(ohlcv_cache),
        "raw_trade_rows": len(raw_rows),
        "valid_signal_eligible_rows": len(rows),
        "ticker_count": len(tickers),
        "date_min": str(days[0].date()) if days else None,
        "date_max": str(days[-1].date()) if days else None,
        "trading_days": len(days),
        "required_trade_fields_missing_from_sample": missing,
        "has_entry_signal_score": "entry_signal_score" in sample,
        "has_daily_recomputed_signal": any(k in sample for k in ["daily_signal_score", "current_signal_score", "signal_score_by_date"]),
        "current_signal_default": "price_path_proxy because stage2 trades carry entry signal but not daily recomputed signal",
        "min_tickers_for_100pct_with_30pct_cap": min_required_tickers,
        "active_ticker_count_min": min(active_counts) if active_counts else 0,
        "active_ticker_count_avg": sum(active_counts) / len(active_counts) if active_counts else 0.0,
        "active_ticker_count_max": max(active_counts) if active_counts else 0,
        "cap_100pct_feasible_days": feasible_days,
        "cap_100pct_feasible_day_pct": feasible_days / len(active_counts) * 100.0 if active_counts else 0.0,
        "ohlcv_pkl_available_for_valid_tickers": pkl_available,
        "parameter_grid": {
            "weak_drop_pct": [10.0, 15.0, 25.0],
            "stronger_gap_pct": [10.0, 20.0, 30.0],
            "signal_to_weight_mode": ["linear", "bucket", "aggressive_linear"],
            "slippage_bps": [0.0, 5.0],
            "lot_mode": ["lot", "ticker_avg_cost_comparison"],
        },
        "will_not_execute_heavy_backtest_in_dry_run": True,
    }


def _config_grid(slippage_bps: float) -> list[LotProbeConfig]:
    configs: list[LotProbeConfig] = []
    for weak in [10.0, 15.0, 25.0]:
        for gap in [10.0, 20.0, 30.0]:
            for mode in ["linear", "bucket", "aggressive_linear"]:
                configs.append(LotProbeConfig(weak_drop_pct=weak, stronger_gap_pct=gap, signal_to_weight_mode=mode, slippage_bps=slippage_bps, lot_mode="lot"))
    return configs


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv

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
    ohlcv_cache: Path = DEFAULT_OHLCV_CACHE,
    out_dir: Path = OUT_DIR,
    limit: Optional[int] = None,
) -> dict[str, Any]:
    trades = load_stage2_trade_candidates(trades_jsonl, limit=limit)
    if not trades:
        raise ValueError(f"no valid stage2 trades: {trades_jsonl}")
    histories = _load_ohlcv_cache(ohlcv_cache, [row["ticker"] for row in trades])
    if not histories:
        raise ValueError(f"no histories loaded from {ohlcv_cache}")

    # Baseline: current fixed analogue under the cap-binding lot framework.
    baseline_config = LotProbeConfig(signal_to_weight_mode="bucket", weak_drop_pct=999.0, stronger_gap_pct=999.0, slippage_bps=5.0, current_signal_mode="entry_constant")
    baseline_net = simulate_lot_rebalance(trades, histories, baseline_config)
    baseline_zero = simulate_lot_rebalance(trades, histories, LotProbeConfig(**{**baseline_config.__dict__, "slippage_bps": 0.0}))

    result_rows: list[dict[str, Any]] = []
    best: Optional[dict[str, Any]] = None
    for slip in [0.0, 5.0]:
        for config in _config_grid(slippage_bps=slip):
            metrics = simulate_lot_rebalance(trades, histories, config)
            base = baseline_zero if slip == 0.0 else baseline_net
            delta = metrics["total_return_net_pct"] - base["total_return_net_pct"]
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
                "time_out_loss_pnl_units": metrics["time_out_loss_pnl_units"],
                "passed_min_effect_after_slippage": bool(slip == 5.0 and delta >= MIN_EFFECT_PCT and metrics["max_ticker_gross_share_pct"] <= MAX_TICKER_SHARE_PCT + 1e-9),
            }
            result_rows.append(row)
            if slip == 5.0 and (best is None or row["return_delta_pct"] > best["return_delta_pct"]):
                best = row

    summary = {
        "gate": "capital_lot_rebalance_probe",
        "stage2_source": str(trades_jsonl),
        "ohlcv_cache": str(ohlcv_cache),
        "out_dir": str(out_dir),
        "trade_count": len(trades),
        "ticker_count": len({row["ticker"] for row in trades}),
        "baseline_fixed_lot_slippage0": baseline_zero,
        "baseline_fixed_lot_slippage5": baseline_net,
        "candidate_count": len(result_rows),
        "best_after_5bps_slippage": best,
        "implementation_recommended_prelim": bool(best and best.get("passed_min_effect_after_slippage")),
        "criteria_note": "Full acceptance also requires year-by-year and leave-one-ticker-out checks; those are intentionally reserved for post-stage2 full execution.",
        "results": result_rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "summary.json", summary)
    _write_csv(out_dir / "probe_results.csv", result_rows)
    return summary
