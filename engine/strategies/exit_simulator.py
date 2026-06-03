"""
청산 시뮬레이터
- 3가지 청산 전략: fixed, trailing, hybrid
- 추가매수(피라미딩) 시뮬레이션
- Phase 2 cutover: 실제 청산 판정은 engine.core.exit_policy.evaluate_exit 사용
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from engine.core.exit_policy import (
    ExitDecision,
    ExitExecutionConfig,
    MarketContext,
    PriceSnapshot,
    evaluate_exit,
    initialize_position_state,
    update_position_for_add_buy,
)
from engine.core.logger import get_logger
from engine.strategies.rulebook import Rulebook

log = get_logger("exit_simulator")


@dataclass
class Trade:
    """단일 거래 결과 (추가매수 포함)"""
    entry_date: str
    entry_price: float
    entry_shares: int
    exit_date: str
    exit_price: float               # base case fill price
    exit_reason: str                # 'take_profit' | 'stop_loss' | 'trailing' | 'time_out' | 'signal_exit'
    holding_days: int
    add_buys: list = field(default_factory=list)  # [(date, price, shares), ...]
    total_shares: int = 0
    avg_cost: float = 0.0
    pnl_pct: float = 0.0            # base case 수익률 (수수료 차감 후)
    pnl_krw: float = 0.0            # base case 손익 금액
    commission: float = 0.0
    trigger_price: Optional[float] = None
    fill_price_base: Optional[float] = None
    fill_price_stress: Optional[float] = None
    stress_pnl_pct: Optional[float] = None
    stress_pnl_krw: Optional[float] = None

    def to_dict(self) -> dict:
        d = {
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "entry_shares": self.entry_shares,
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "exit_reason": self.exit_reason,
            "holding_days": self.holding_days,
            "add_buys": [
                {"date": d, "price": round(p, 2), "shares": s}
                for d, p, s in self.add_buys
            ],
            "total_shares": self.total_shares,
            "avg_cost": round(self.avg_cost, 2),
            "pnl_pct": round(self.pnl_pct, 3),
            "pnl_krw": round(self.pnl_krw, 0),
            "commission": round(self.commission, 0),
        }
        if self.trigger_price is not None:
            d["trigger_price"] = round(self.trigger_price, 4)
        if self.fill_price_base is not None:
            d["fill_price_base"] = round(self.fill_price_base, 4)
        if self.fill_price_stress is not None:
            d["fill_price_stress"] = round(self.fill_price_stress, 4)
        if self.stress_pnl_pct is not None:
            d["stress_pnl_pct"] = round(self.stress_pnl_pct, 3)
        if self.stress_pnl_krw is not None:
            d["stress_pnl_krw"] = round(self.stress_pnl_krw, 0)
        return d


def _exit_shadow_enabled() -> bool:
    return str(os.environ.get("EXIT_SHADOW", "")).strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _row_date(row) -> str:
    return str(row.name.date()) if hasattr(row.name, "date") else str(row.name)


def _shadow_log_path(ticker: str) -> Path:
    safe_ticker = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(ticker or "UNKNOWN"))
    return Path("logs") / f"exit_shadow_{safe_ticker}.jsonl"


def _classify_exit_shadow_difference(
    rb: Rulebook,
    legacy_reason: Optional[str],
    legacy_price: Optional[float],
    new_reason: Optional[str],
    new_trigger: Optional[float],
    new_fill_base: Optional[float],
    diagnostics: dict,
) -> str:
    if legacy_reason == new_reason:
        if legacy_reason is None:
            return "SAME"
        if legacy_price is not None and new_fill_base is not None and abs(float(legacy_price) - float(new_fill_base)) > 1e-9:
            return "INTENTIONAL_SLIPPAGE"
        if legacy_price is not None and new_trigger is not None and abs(float(legacy_price) - float(new_trigger)) > 1e-9:
            return "INTENTIONAL_SLIPPAGE"
        return "SAME"

    strategy = str(getattr(rb, "exit_strategy", "") or "").lower()
    if strategy == "hybrid":
        if legacy_reason == "take_profit" and new_reason in {"stop_loss", "trailing"}:
            return "INTENTIONAL_HYBRID_PRIORITY"
        if legacy_reason == "stop_loss" and new_reason == "trailing":
            return "INTENTIONAL_HYBRID_PRIORITY"

    if bool(diagnostics.get("trailing_delay_difference")):
        return "INTENTIONAL_TRAILING_DELAY"

    if bool(diagnostics.get("dynamic_exit_difference")):
        return "INTENTIONAL_DYNAMIC_EXIT"

    if legacy_reason == "time_out" or new_reason == "time_out":
        if bool(diagnostics.get("timeout_boundary")):
            return "INTENTIONAL_TRADING_DAY_TIMEOUT"

    return "BUG_CANDIDATE"


def _write_exit_shadow_record(record: dict) -> None:
    try:
        path = _shadow_log_path(record.get("ticker", "UNKNOWN"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as e:
        log.warning(f"exit shadow log write failed: {e}")


def _shadow_compare_exit(
    rb: Rulebook,
    row,
    entry_date: str,
    holding_days: int,
    legacy_ref: Optional[dict],
    new_decision: ExitDecision,
    inputs: dict[str, Any],
) -> None:
    """Compare new default ExitPolicy decision against legacy reference."""
    if not _exit_shadow_enabled():
        return

    try:
        ticker = str(getattr(rb, "ticker", "UNKNOWN") or "UNKNOWN")
        legacy_reason = legacy_ref.get("reason") if legacy_ref else None
        legacy_price = legacy_ref.get("price") if legacy_ref else None
        new_reason = new_decision.reason
        new_trigger = new_decision.trigger_price
        new_fill_base = new_decision.fill_price_base
        diagnostics = dict(new_decision.diagnostics or {})

        diagnostics["dynamic_exit_difference"] = False
        diagnostics["trailing_delay_difference"] = (
            legacy_reason == "trailing" and new_reason is None and holding_days <= 2
        ) or (
            legacy_reason is None and new_reason == "trailing" and holding_days <= 2
        )
        diagnostics["timeout_boundary"] = holding_days >= int(getattr(rb, "max_holding_days", holding_days))

        difference_type = _classify_exit_shadow_difference(
            rb,
            legacy_reason,
            legacy_price,
            new_reason,
            new_trigger,
            new_fill_base,
            diagnostics,
        )

        record = {
            "ticker": ticker,
            "date": _row_date(row),
            "entry_date": entry_date,
            "holding_days": int(holding_days),
            "legacy": {
                "reason": legacy_reason,
                "price": legacy_price,
            },
            "new": {
                "reason": new_reason,
                "trigger_price": new_trigger,
                "fill_price_base": new_decision.fill_price_base,
                "fill_price_stress": new_decision.fill_price_stress,
            },
            "difference_type": difference_type,
            "inputs": inputs,
            "diagnostics": diagnostics,
        }
        _write_exit_shadow_record(record)
    except Exception as e:
        log.warning(f"exit shadow compare failed: {e}")


def _legacy_decision_for_bar(
    rb: Rulebook,
    is_short: bool,
    high: float,
    low: float,
    stop_loss: float,
    take_profit: float,
    trailing_stop: float,
    holding_days: int,
    include_timeout: bool = False,
) -> tuple[Optional[str], Optional[float]]:
    """Legacy one-bar exit decision used only for shadow/regression reference."""
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None

    if rb.exit_strategy in ("fixed", "hybrid"):
        if not is_short:
            if low <= stop_loss:
                exit_price = stop_loss
                exit_reason = "stop_loss"
            elif high >= take_profit:
                exit_price = take_profit
                exit_reason = "take_profit"
        else:
            if high >= stop_loss:
                exit_price = stop_loss
                exit_reason = "stop_loss"
            elif low <= take_profit:
                exit_price = take_profit
                exit_reason = "take_profit"

    if exit_price is None and rb.exit_strategy in ("trailing", "hybrid"):
        if not is_short:
            if low <= trailing_stop and holding_days > 2:
                exit_price = trailing_stop
                exit_reason = "trailing"
        else:
            if high >= trailing_stop and holding_days > 2:
                exit_price = trailing_stop
                exit_reason = "trailing"

    if exit_price is None and include_timeout and holding_days >= int(rb.max_holding_days):
        exit_reason = "time_out"
        exit_price = None

    return exit_reason, exit_price


def _simulate_exit_legacy_reference(
    rb: Rulebook,
    df: pd.DataFrame,
    entry_idx: int,
    initial_shares: int,
    initial_budget_krw: float,
    commission_rate: float = 0.0005,
    cur_market_score: float = 50.0,
    cur_vix_level: float = 18.0,
    collect_trace: bool = False,
) -> Optional[Trade] | tuple[Optional[Trade], list[dict]]:
    """Original legacy implementation preserved for shadow regression only."""
    trace: list[dict] = []

    if entry_idx + 1 >= len(df):
        return (None, trace) if collect_trace else None

    is_short = (rb.direction == "short")
    entry_row = df.iloc[entry_idx]
    entry_price = float(entry_row["Close"])
    if entry_price <= 0 or pd.isna(entry_price):
        return (None, trace) if collect_trace else None

    atr = float(entry_row.get("ATR", entry_price * 0.02))
    if pd.isna(atr) or atr <= 0:
        atr = entry_price * 0.02

    from engine.strategies.evaluator import get_dynamic_exit_params
    dyn_sl_atr, dyn_tp_atr, dyn_trail_atr = get_dynamic_exit_params(
        rb, market_score=cur_market_score, vix_level=cur_vix_level
    )

    if not is_short:
        stop_loss = entry_price - atr * dyn_sl_atr
        take_profit = entry_price + atr * dyn_tp_atr
    else:
        stop_loss = entry_price + atr * dyn_sl_atr
        take_profit = entry_price - atr * dyn_tp_atr

    extreme = entry_price
    total_shares = initial_shares
    used_krw = entry_price * initial_shares
    add_buys: list = []
    avg_cost = entry_price
    entry_date = str(df.index[entry_idx].date())
    trailing_stop = extreme

    for i in range(entry_idx + 1, min(entry_idx + rb.max_holding_days + 1, len(df))):
        row = df.iloc[i]
        high = float(row.get("High", row["Close"]))
        low = float(row.get("Low", row["Close"]))
        close = float(row["Close"])
        holding_days = i - entry_idx

        if not is_short:
            current_pnl_pct = (close - avg_cost) / avg_cost * 100
            extreme = max(extreme, high)
            trailing_stop = extreme - atr * dyn_trail_atr
        else:
            current_pnl_pct = (avg_cost - close) / avg_cost * 100
            extreme = min(extreme, low)
            trailing_stop = extreme + atr * dyn_trail_atr

        if (
            rb.add_buy_enabled
            and len(add_buys) < rb.add_buy_max_count
            and current_pnl_pct >= rb.add_buy_trigger_profit_pct
        ):
            add_budget = used_krw * rb.add_buy_size_ratio
            remaining = initial_budget_krw - used_krw
            if remaining > add_budget * 0.5:
                add_budget = min(add_budget, remaining)
                add_price = close
                add_shares = int(add_budget / add_price)
                if add_shares > 0:
                    add_buys.append((str(row.name.date()), add_price, add_shares))
                    new_total = total_shares + add_shares
                    avg_cost = (avg_cost * total_shares + add_price * add_shares) / new_total
                    total_shares = new_total
                    used_krw += add_price * add_shares
                    if not is_short:
                        stop_loss = avg_cost - atr * dyn_sl_atr
                        take_profit = avg_cost + atr * dyn_tp_atr
                    else:
                        stop_loss = avg_cost + atr * dyn_sl_atr
                        take_profit = avg_cost - atr * dyn_tp_atr

        exit_reason, exit_price = _legacy_decision_for_bar(
            rb, is_short, high, low, stop_loss, take_profit, trailing_stop, holding_days
        )
        trace.append(
            {
                "date": _row_date(row),
                "holding_days": holding_days,
                "reason": exit_reason,
                "price": exit_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "trailing_stop": trailing_stop,
                "extreme": extreme,
            }
        )

        if exit_price is not None:
            trade = _build_trade(
                entry_date, entry_price, initial_shares,
                row.name, exit_price, exit_reason, holding_days,
                add_buys, total_shares, avg_cost, is_short, commission_rate,
            )
            return (trade, trace) if collect_trace else trade

    last_row = df.iloc[min(entry_idx + rb.max_holding_days, len(df) - 1)]
    legacy_holding_days = min(rb.max_holding_days, len(df) - 1 - entry_idx)
    if collect_trace:
        trace.append(
            {
                "date": _row_date(last_row),
                "holding_days": legacy_holding_days,
                "reason": "time_out",
                "price": float(last_row["Close"]),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "trailing_stop": trailing_stop,
                "extreme": extreme,
            }
        )
    trade = _build_trade(
        entry_date, entry_price, initial_shares,
        last_row.name, float(last_row["Close"]), "time_out",
        legacy_holding_days,
        add_buys, total_shares, avg_cost, is_short, commission_rate,
    )
    return (trade, trace) if collect_trace else trade


def _trace_by_holding_days(trace: list[dict]) -> dict[int, dict]:
    return {int(item.get("holding_days", -1)): item for item in trace}


def _make_price_snapshot(df: pd.DataFrame, i: int) -> PriceSnapshot:
    row = df.iloc[i]
    next_row = df.iloc[i + 1] if i + 1 < len(df) else None
    return PriceSnapshot(
        date=_row_date(row),
        open=_safe_float(row.get("Open", row.get("Close"))),
        high=_safe_float(row.get("High", row.get("Close"))),
        low=_safe_float(row.get("Low", row.get("Close"))),
        close=_safe_float(row.get("Close")),
        next_open=_safe_float(next_row.get("Open", next_row.get("Close"))) if next_row is not None else None,
    )


def simulate_exit(
    rb: Rulebook,
    df: pd.DataFrame,
    entry_idx: int,
    initial_shares: int,
    initial_budget_krw: float,
    commission_rate: float = 0.0005,
    cur_market_score: float = 50.0,
    cur_vix_level: float = 18.0,
) -> Optional[Trade]:
    """
    entry_idx 시점에 진입했다고 가정하고 청산까지 시뮬레이션.

    Phase 2부터 실제 청산 판정은 ExitPolicy(evaluate_exit)가 담당한다.
    EXIT_SHADOW=1이면 legacy reference를 병렬 계산해 jsonl에 비교 기록만 남긴다.
    """
    if entry_idx + 1 >= len(df):
        return None

    direction = str(getattr(rb, "direction", "long") or "long").lower()
    if direction != "long":
        raise NotImplementedError("ExitPolicy cutover supports long-only backtests; short/inverse is deferred.")

    entry_row = df.iloc[entry_idx]
    entry_price = float(entry_row["Close"])
    if entry_price <= 0 or pd.isna(entry_price):
        return None

    atr = float(entry_row.get("ATR", entry_price * 0.02))
    if pd.isna(atr) or atr <= 0:
        atr = entry_price * 0.02

    entry_date = str(df.index[entry_idx].date())
    market_context = MarketContext(market_score=cur_market_score, vix_level=cur_vix_level)
    execution_config = ExitExecutionConfig(trailing_activation_bars=2)
    position = initialize_position_state(
        ticker=str(getattr(rb, "ticker", "") or ""),
        entry_price=entry_price,
        shares=initial_shares,
        rulebook=rb,
        atr_value=atr,
        market_context=market_context,
        entry_date=entry_date,
    )

    used_krw = entry_price * initial_shares
    add_buys: list = []

    legacy_trace_by_day: dict[int, dict] = {}
    if _exit_shadow_enabled():
        _, legacy_trace = _simulate_exit_legacy_reference(
            rb,
            df,
            entry_idx,
            initial_shares,
            initial_budget_krw,
            commission_rate=commission_rate,
            cur_market_score=cur_market_score,
            cur_vix_level=cur_vix_level,
            collect_trace=True,
        )
        legacy_trace_by_day = _trace_by_holding_days(legacy_trace)

    for i in range(entry_idx + 1, min(entry_idx + rb.max_holding_days + 1, len(df))):
        row = df.iloc[i]
        close = float(row["Close"])
        holding_days = i - entry_idx

        current_pnl_pct = (close - position.avg_cost) / position.avg_cost * 100 if position.avg_cost > 0 else 0.0
        if (
            rb.add_buy_enabled
            and position.add_buy_count < rb.add_buy_max_count
            and current_pnl_pct >= rb.add_buy_trigger_profit_pct
        ):
            add_budget = used_krw * rb.add_buy_size_ratio
            remaining = initial_budget_krw - used_krw
            if remaining > add_budget * 0.5:
                add_budget = min(add_budget, remaining)
                add_price = close
                add_shares = int(add_budget / add_price)
                if add_shares > 0:
                    add_buys.append((str(row.name.date()), add_price, add_shares))
                    used_krw += add_price * add_shares
                    position = update_position_for_add_buy(
                        position,
                        add_price=add_price,
                        add_shares=add_shares,
                        rulebook=rb,
                        atr_value=atr,
                        market_context=market_context,
                    )

        price_snapshot = _make_price_snapshot(df, i)
        bar_context = MarketContext(
            market_score=cur_market_score,
            vix_level=cur_vix_level,
            holding_trading_days=holding_days,
            current_trade_date=price_snapshot.date,
        )
        decision = evaluate_exit(position, price_snapshot, rb, bar_context, execution_config)
        if decision.updated_position is not None:
            position = decision.updated_position

        if _exit_shadow_enabled():
            inputs = {
                "open": price_snapshot.open,
                "high": price_snapshot.high,
                "low": price_snapshot.low,
                "close": price_snapshot.close,
                "next_open": price_snapshot.next_open,
                "atr": float(atr),
                "entry_price": float(entry_price),
                "avg_cost": float(position.avg_cost),
                "stop_loss": float(position.stop_price),
                "take_profit": float(position.target_price),
                "trailing_stop": float(position.trailing_stop),
                "highest_price": float(position.highest_price),
                "exit_strategy": str(getattr(rb, "exit_strategy", "")),
                "direction": direction,
            }
            _shadow_compare_exit(
                rb=rb,
                row=row,
                entry_date=entry_date,
                holding_days=holding_days,
                legacy_ref=legacy_trace_by_day.get(holding_days),
                new_decision=decision,
                inputs=inputs,
            )

        if decision.should_exit:
            exit_price = decision.fill_price_base if decision.fill_price_base is not None else decision.trigger_price
            if exit_price is None:
                exit_price = float(row["Close"])
            return _build_trade(
                entry_date, entry_price, initial_shares,
                row.name, float(exit_price), str(decision.reason), holding_days,
                add_buys, int(position.shares), float(position.avg_cost), False, commission_rate,
                trigger_price=decision.trigger_price,
                fill_price_base=decision.fill_price_base,
                fill_price_stress=decision.fill_price_stress,
            )

    # 데이터가 max_holding_days 전에 끝난 경우의 안전 fallback.
    last_row = df.iloc[min(entry_idx + rb.max_holding_days, len(df) - 1)]
    holding_days = min(rb.max_holding_days, len(df) - 1 - entry_idx)
    exit_price = float(last_row["Close"])
    return _build_trade(
        entry_date, entry_price, initial_shares,
        last_row.name, exit_price, "time_out",
        holding_days,
        add_buys, int(position.shares), float(position.avg_cost), False, commission_rate,
        trigger_price=exit_price,
        fill_price_base=exit_price,
        fill_price_stress=exit_price,
    )


def _build_trade(
    entry_date, entry_price, initial_shares,
    exit_idx, exit_price, exit_reason, holding_days,
    add_buys, total_shares, avg_cost, is_short, commission_rate,
    trigger_price: Optional[float] = None,
    fill_price_base: Optional[float] = None,
    fill_price_stress: Optional[float] = None,
) -> Trade:
    if is_short:
        gross_pnl_pct = (avg_cost - exit_price) / avg_cost * 100
    else:
        gross_pnl_pct = (exit_price - avg_cost) / avg_cost * 100

    # 수수료: 매수 + 매도 (왕복)
    commission = (avg_cost * total_shares + exit_price * total_shares) * (commission_rate / 2)
    net_pnl_krw = (exit_price - avg_cost) * total_shares * (-1 if is_short else 1) - commission
    net_pnl_pct = net_pnl_krw / (avg_cost * total_shares) * 100

    stress_pnl_krw = None
    stress_pnl_pct = None
    if fill_price_stress is not None:
        stress_commission = (avg_cost * total_shares + fill_price_stress * total_shares) * (commission_rate / 2)
        stress_pnl_krw = (fill_price_stress - avg_cost) * total_shares * (-1 if is_short else 1) - stress_commission
        stress_pnl_pct = stress_pnl_krw / (avg_cost * total_shares) * 100

    return Trade(
        entry_date=entry_date,
        entry_price=entry_price,
        entry_shares=initial_shares,
        exit_date=str(exit_idx.date()),
        exit_price=exit_price,
        exit_reason=exit_reason,
        holding_days=holding_days,
        add_buys=add_buys,
        total_shares=total_shares,
        avg_cost=avg_cost,
        pnl_pct=net_pnl_pct,
        pnl_krw=net_pnl_krw,
        commission=commission,
        trigger_price=trigger_price,
        fill_price_base=fill_price_base if fill_price_base is not None else exit_price,
        fill_price_stress=fill_price_stress,
        stress_pnl_pct=stress_pnl_pct,
        stress_pnl_krw=stress_pnl_krw,
    )


if __name__ == "__main__":
    import numpy as np
    from engine.core.indicators import calc_indicators
    from engine.strategies.rulebook import default_rulebook

    np.random.seed(7)
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    # 상승 추세 가짜 데이터
    close = 25000 + np.cumsum(np.random.randn(n) * 50 + 20)
    df = pd.DataFrame(
        {
            "Open": close + np.random.randn(n) * 30,
            "High": close + np.abs(np.random.randn(n)) * 80,
            "Low": close - np.abs(np.random.randn(n)) * 80,
            "Close": close,
            "Volume": np.random.randint(10000, 50000, n),
        },
        index=idx,
    )
    df = calc_indicators(df)

    rb = default_rulebook("TEST", "korean_etf", "long")
    rb.exit_strategy = "hybrid"
    rb.stop_loss_atr = 2.0
    rb.take_profit_atr = 3.0
    rb.trailing_atr = 1.5
    rb.max_holding_days = 20
    rb.add_buy_enabled = True
    rb.add_buy_trigger_profit_pct = 1.5
    rb.add_buy_max_count = 2
    rb.add_buy_size_ratio = 0.5

    # 30번째 봉에서 4주 매수, 한도 120,000원 가정
    trade = simulate_exit(rb, df, entry_idx=30, initial_shares=4, initial_budget_krw=120000)
    print("=" * 60)
    print("청산 시뮬레이션 결과 (LONG, hybrid 전략, 추가매수 활성, ExitPolicy)")
    print("=" * 60)
    if trade:
        for k, v in trade.to_dict().items():
            print(f"  {k:18}: {v}")
    else:
        print("  거래 없음 (데이터 부족)")
