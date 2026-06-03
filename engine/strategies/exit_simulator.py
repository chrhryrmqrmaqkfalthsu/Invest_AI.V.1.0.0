"""
청산 시뮬레이터
- 3가지 청산 전략: fixed, trailing, hybrid
- 추가매수(피라미딩) 시뮬레이션
- 백테스트와 실전 둘 다에서 사용
"""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd

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
    exit_price: float
    exit_reason: str           # 'take_profit' | 'stop_loss' | 'trailing' | 'time_out' | 'signal_exit'
    holding_days: int
    add_buys: list = field(default_factory=list)  # [(date, price, shares), ...]
    total_shares: int = 0
    avg_cost: float = 0.0
    pnl_pct: float = 0.0       # 수익률 (수수료 차감 후)
    pnl_krw: float = 0.0       # 손익 금액
    commission: float = 0.0

    def to_dict(self) -> dict:
        return {
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


def _exit_shadow_enabled() -> bool:
    return str(os.environ.get("EXIT_SHADOW", "")).strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _shadow_log_path(ticker: str) -> Path:
    safe_ticker = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(ticker or "UNKNOWN"))
    return Path("logs") / f"exit_shadow_{safe_ticker}.jsonl"


def _classify_exit_shadow_difference(
    rb: Rulebook,
    legacy_reason: Optional[str],
    legacy_price: Optional[float],
    new_reason: Optional[str],
    new_trigger: Optional[float],
    diagnostics: dict,
) -> str:
    if legacy_reason == new_reason:
        if legacy_reason is None:
            return "SAME"
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
    next_row,
    entry_date: str,
    entry_price: float,
    avg_cost: float,
    total_shares: float,
    atr: float,
    stop_loss: float,
    take_profit: float,
    trailing_stop: float,
    extreme: float,
    legacy_reason: Optional[str],
    legacy_price: Optional[float],
    holding_days: int,
    cur_market_score: float,
    cur_vix_level: float,
    dyn_sl_atr: float,
    dyn_tp_atr: float,
    dyn_trail_atr: float,
) -> None:
    if not _exit_shadow_enabled():
        return

    try:
        from engine.core.exit_policy import (
            ExitExecutionConfig,
            MarketContext,
            PositionState,
            PriceSnapshot,
            evaluate_exit,
            resolve_exit_params,
        )

        ticker = str(getattr(rb, "ticker", "UNKNOWN") or "UNKNOWN")
        direction = str(getattr(rb, "direction", "long") or "long").lower()
        new_reason = None
        new_trigger = None
        fill_base = None
        fill_stress = None
        new_diagnostics = {}

        if direction != "long":
            new_diagnostics["unsupported_direction"] = direction
        else:
            position = PositionState(
                ticker=ticker,
                direction="long",
                entry_date=entry_date,
                entry_price=float(entry_price),
                avg_cost=float(avg_cost),
                shares=float(total_shares),
                atr_at_entry=float(atr),
                stop_price=float(stop_loss),
                target_price=float(take_profit),
                trailing_stop=float(trailing_stop),
                trailing_distance=float(atr) * float(dyn_trail_atr),
                highest_price=float(extreme),
                max_holding_days=int(getattr(rb, "max_holding_days", holding_days)),
                exit_strategy=str(getattr(rb, "exit_strategy", "hybrid") or "hybrid"),
                holding_trading_days=int(holding_days),
            )
            price = PriceSnapshot(
                date=str(row.name.date()) if hasattr(row.name, "date") else str(row.name),
                open=_safe_float(row.get("Open", row.get("Close"))),
                high=_safe_float(row.get("High", row.get("Close"))),
                low=_safe_float(row.get("Low", row.get("Close"))),
                close=_safe_float(row.get("Close")),
                next_open=_safe_float(next_row.get("Open", next_row.get("Close"))) if next_row is not None else None,
            )
            market_context = MarketContext(
                market_score=float(cur_market_score),
                vix_level=float(cur_vix_level),
                holding_trading_days=int(holding_days),
            )
            execution_config = ExitExecutionConfig(trailing_activation_bars=2)
            decision = evaluate_exit(position, price, rb, market_context, execution_config)
            new_reason = decision.reason
            new_trigger = decision.trigger_price
            fill_base = decision.fill_price_base
            fill_stress = decision.fill_price_stress
            new_diagnostics = dict(decision.diagnostics or {})

            shadow_sl, shadow_tp, shadow_trail = resolve_exit_params(rb, market_context)
            new_diagnostics["dynamic_exit_difference"] = any(
                abs(float(a) - float(b)) > 1e-9
                for a, b in ((dyn_sl_atr, shadow_sl), (dyn_tp_atr, shadow_tp), (dyn_trail_atr, shadow_trail))
            )
            new_diagnostics["trailing_delay_difference"] = (
                legacy_reason == "trailing" and new_reason is None and holding_days <= execution_config.trailing_activation_bars
            ) or (
                legacy_reason is None and new_reason == "trailing" and holding_days <= execution_config.trailing_activation_bars
            )
            new_diagnostics["timeout_boundary"] = holding_days >= int(getattr(rb, "max_holding_days", holding_days))

        difference_type = _classify_exit_shadow_difference(
            rb, legacy_reason, legacy_price, new_reason, new_trigger, new_diagnostics
        )

        record = {
            "ticker": ticker,
            "date": str(row.name.date()) if hasattr(row.name, "date") else str(row.name),
            "entry_date": entry_date,
            "holding_days": int(holding_days),
            "legacy": {
                "reason": legacy_reason,
                "price": legacy_price,
            },
            "new": {
                "reason": new_reason,
                "trigger_price": new_trigger,
                "fill_price_base": fill_base,
                "fill_price_stress": fill_stress,
            },
            "difference_type": difference_type,
            "inputs": {
                "open": _safe_float(row.get("Open", row.get("Close"))),
                "high": _safe_float(row.get("High", row.get("Close"))),
                "low": _safe_float(row.get("Low", row.get("Close"))),
                "close": _safe_float(row.get("Close")),
                "atr": float(atr),
                "entry_price": float(entry_price),
                "avg_cost": float(avg_cost),
                "stop_loss": float(stop_loss),
                "take_profit": float(take_profit),
                "trailing_stop": float(trailing_stop),
                "extreme": float(extreme),
                "exit_strategy": str(getattr(rb, "exit_strategy", "")),
                "direction": str(getattr(rb, "direction", "")),
                "dynamic_atr": {
                    "stop_loss_atr": float(dyn_sl_atr),
                    "take_profit_atr": float(dyn_tp_atr),
                    "trailing_atr": float(dyn_trail_atr),
                },
            },
            "diagnostics": new_diagnostics,
        }
        _write_exit_shadow_record(record)
    except Exception as e:
        log.warning(f"exit shadow compare failed: {e}")


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

    Args:
        rb: 룰북
        df: OHLCV+지표 DataFrame
        entry_idx: 진입 시점 인덱스
        initial_shares: 초기 매수 주수
        initial_budget_krw: 한도 (추가매수도 이 안에서)
        commission_rate: 왕복 수수료 비율
        cur_market_score: 진입 시점 시장 점수 (v5: 동적 손절익절용)
        cur_vix_level: 진입 시점 VIX (v5: 동적 손절익절용)

    Returns:
        Trade 또는 None (데이터 부족 시)
    """
    if entry_idx + 1 >= len(df):
        return None

    is_short = (rb.direction == "short")
    entry_row = df.iloc[entry_idx]
    entry_price = float(entry_row["Close"])
    if entry_price <= 0 or pd.isna(entry_price):
        return None

    atr = float(entry_row.get("ATR", entry_price * 0.02))
    if pd.isna(atr) or atr <= 0:
        atr = entry_price * 0.02

    # 동적 손절익절 ATR (v5 신규): 진입 시점 시장 상태에 따라
    from engine.strategies.evaluator import get_dynamic_exit_params
    dyn_sl_atr, dyn_tp_atr, dyn_trail_atr = get_dynamic_exit_params(
        rb, market_score=cur_market_score, vix_level=cur_vix_level
    )

    # 손절/익절 가격 (방향 따라 부호 반전)
    if not is_short:
        stop_loss = entry_price - atr * dyn_sl_atr
        take_profit = entry_price + atr * dyn_tp_atr
    else:
        stop_loss = entry_price + atr * dyn_sl_atr       # 인버스: 위로 손절
        take_profit = entry_price - atr * dyn_tp_atr     # 인버스: 아래로 익절

    # 트레일링용 최고점 (long) / 최저점 (short)
    extreme = entry_price

    # 누적 포지션
    total_shares = initial_shares
    used_krw = entry_price * initial_shares
    add_buys: list = []
    avg_cost = entry_price

    entry_date = str(df.index[entry_idx].date())

    for i in range(entry_idx + 1, min(entry_idx + rb.max_holding_days + 1, len(df))):
        row = df.iloc[i]
        high = float(row.get("High", row["Close"]))
        low = float(row.get("Low", row["Close"]))
        close = float(row["Close"])

        # ----- 손익 추적 (방향 따라 다름) -----
        if not is_short:
            current_pnl_pct = (close - avg_cost) / avg_cost * 100
            extreme = max(extreme, high)
            trailing_stop = extreme - atr * dyn_trail_atr
        else:
            current_pnl_pct = (avg_cost - close) / avg_cost * 100
            extreme = min(extreme, low)
            trailing_stop = extreme + atr * dyn_trail_atr

        # ----- 추가매수 체크 -----
        if (
            rb.add_buy_enabled
            and len(add_buys) < rb.add_buy_max_count
            and current_pnl_pct >= rb.add_buy_trigger_profit_pct
        ):
            add_budget = used_krw * rb.add_buy_size_ratio
            remaining = initial_budget_krw - used_krw
            if remaining > add_budget * 0.5:  # 최소 절반은 가능해야 추가
                add_budget = min(add_budget, remaining)
                add_price = close
                add_shares = int(add_budget / add_price)
                if add_shares > 0:
                    add_buys.append((str(row.name.date()), add_price, add_shares))
                    new_total = total_shares + add_shares
                    avg_cost = (avg_cost * total_shares + add_price * add_shares) / new_total
                    total_shares = new_total
                    used_krw += add_price * add_shares
                    # 추가매수 후 손절가 재계산 (avg_cost 기준)
                    if not is_short:
                        stop_loss = avg_cost - atr * dyn_sl_atr
                        take_profit = avg_cost + atr * dyn_tp_atr
                    else:
                        stop_loss = avg_cost + atr * dyn_sl_atr
                        take_profit = avg_cost - atr * dyn_tp_atr

        # ----- 청산 조건 체크 (우선순위: 손절 > 익절 > 트레일링) -----
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
                if low <= trailing_stop and i > entry_idx + 2:
                    exit_price = trailing_stop
                    exit_reason = "trailing"
            else:
                if high >= trailing_stop and i > entry_idx + 2:
                    exit_price = trailing_stop
                    exit_reason = "trailing"

        next_row = df.iloc[i + 1] if i + 1 < len(df) else None
        _shadow_compare_exit(
            rb=rb,
            row=row,
            next_row=next_row,
            entry_date=entry_date,
            entry_price=entry_price,
            avg_cost=avg_cost,
            total_shares=total_shares,
            atr=atr,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=trailing_stop,
            extreme=extreme,
            legacy_reason=exit_reason,
            legacy_price=exit_price,
            holding_days=i - entry_idx,
            cur_market_score=cur_market_score,
            cur_vix_level=cur_vix_level,
            dyn_sl_atr=dyn_sl_atr,
            dyn_tp_atr=dyn_tp_atr,
            dyn_trail_atr=dyn_trail_atr,
        )

        if exit_price is not None:
            return _build_trade(
                entry_date, entry_price, initial_shares,
                row.name, exit_price, exit_reason, i - entry_idx,
                add_buys, total_shares, avg_cost, is_short, commission_rate,
            )

    # 시간 초과 청산
    last_row = df.iloc[min(entry_idx + rb.max_holding_days, len(df) - 1)]
    legacy_holding_days = min(rb.max_holding_days, len(df) - 1 - entry_idx)
    _shadow_compare_exit(
        rb=rb,
        row=last_row,
        next_row=None,
        entry_date=entry_date,
        entry_price=entry_price,
        avg_cost=avg_cost,
        total_shares=total_shares,
        atr=atr,
        stop_loss=stop_loss,
        take_profit=take_profit,
        trailing_stop=trailing_stop if "trailing_stop" in locals() else extreme,
        extreme=extreme,
        legacy_reason="time_out",
        legacy_price=float(last_row["Close"]),
        holding_days=legacy_holding_days,
        cur_market_score=cur_market_score,
        cur_vix_level=cur_vix_level,
        dyn_sl_atr=dyn_sl_atr,
        dyn_tp_atr=dyn_tp_atr,
        dyn_trail_atr=dyn_trail_atr,
    )
    return _build_trade(
        entry_date, entry_price, initial_shares,
        last_row.name, float(last_row["Close"]), "time_out",
        legacy_holding_days,
        add_buys, total_shares, avg_cost, is_short, commission_rate,
    )


def _build_trade(
    entry_date, entry_price, initial_shares,
    exit_idx, exit_price, exit_reason, holding_days,
    add_buys, total_shares, avg_cost, is_short, commission_rate,
) -> Trade:
    if is_short:
        gross_pnl_pct = (avg_cost - exit_price) / avg_cost * 100
    else:
        gross_pnl_pct = (exit_price - avg_cost) / avg_cost * 100

    # 수수료: 매수 + 매도 (왕복)
    commission = (avg_cost * total_shares + exit_price * total_shares) * (commission_rate / 2)
    net_pnl_krw = (exit_price - avg_cost) * total_shares * (-1 if is_short else 1) - commission
    net_pnl_pct = net_pnl_krw / (avg_cost * total_shares) * 100

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
    print("청산 시뮬레이션 결과 (LONG, hybrid 전략, 추가매수 활성)")
    print("=" * 60)
    if trade:
        for k, v in trade.to_dict().items():
            print(f"  {k:14}: {v}")
    else:
        print("  거래 없음 (데이터 부족)")
