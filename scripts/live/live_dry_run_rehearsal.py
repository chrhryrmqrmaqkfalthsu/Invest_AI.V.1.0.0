#!/usr/bin/env python3
"""One-shot live dry-run rehearsal for Kingmaker.

RUN/real state safe: this script exercises Runner with isolated temporary state
files and an in-memory paper broker. It does not touch paper_state.json,
positions.json, safety_state.json, pending_orders.json, approvals.json, or
Telegram.

Covered path:
    market open -> startup check -> signal evaluation -> safety gate -> order
    -> immediate paper fill -> BUY reconciliation -> PositionManager save
    -> entry notification -> daily summary notification

Usage:
    venv/bin/python scripts/live/live_dry_run_rehearsal.py
    venv/bin/python scripts/live/live_dry_run_rehearsal.py --ticker AAPL --order-notional 30
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.broker.base import Balance, Broker, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.runner import Runner
from engine.live.safety.layer import SafetyLayer
from engine.strategies.demo_rulebook import Signal, SignalResult
from engine.strategies.rulebook import Rulebook, default_rulebook

log = logging.getLogger("live_dry_run_rehearsal")
KST = ZoneInfo("Asia/Seoul")
SHARE_EPS = 1e-9


@dataclass
class RehearsalResult:
    ok: bool
    artifact_dir: str
    ticker: str
    orders_attempted: int
    orders_filled: int
    signals_buy: int
    signals_hold: int
    broker_orders: int
    broker_holdings: int
    notifier_events: int
    positions_saved: bool
    trade_log_exists: bool
    errors: list[str]

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class AlwaysOpenClock:
    name = "US"
    calendar_source = "rehearsal_always_open"

    def is_open(self, value: Optional[datetime] = None) -> bool:
        return True

    def is_business_day(self, value: Optional[datetime] = None) -> bool:
        return True


class RehearsalNotifier:
    """Telegram-compatible capture sink."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def _record(self, kind: str, payload: object) -> bool:
        self.events.append({"kind": kind, "payload": str(payload)[:1000]})
        return True

    def send(self, text: str, parse_mode: str = "") -> bool:
        return self._record("send", text)

    def send_error(self, message: str) -> bool:
        return self._record("error", message)

    def send_order(self, order) -> bool:
        return self._record("order", getattr(order, "to_dict", lambda: str(order))())

    def send_safety_block(self, code: str, reason: str) -> bool:
        return self._record("safety_block", {"code": code, "reason": reason})

    def send_trade_entry(self, *args, **kwargs) -> bool:
        order = kwargs.get("order")
        payload = {
            "ticker": kwargs.get("ticker") or getattr(order, "ticker", ""),
            "order_id": getattr(order, "order_id", ""),
            "filled_shares": getattr(order, "filled_shares", 0.0),
            "filled_avg_price": getattr(order, "filled_avg_price", 0.0),
            "raw_reason": kwargs.get("raw_reason", ""),
        }
        return self._record("trade_entry", payload)

    def send_trade_exit(self, *args, **kwargs) -> bool:
        return self._record("trade_exit", {"args": args, "kwargs": kwargs})

    def send_approval_request(self, req) -> bool:
        return self._record("approval_request", getattr(req, "to_dict", lambda: str(req))())


class RehearsalPaperBroker(Broker):
    """Deterministic in-memory paper broker.

    This intentionally avoids yfinance/KIS/Alpaca and does not read/write
    data/_system/paper_state.json.
    """

    def __init__(self, *, initial_cash: float, prices: dict[str, float], market_open: bool = True) -> None:
        self.cash = float(initial_cash)
        self.prices = {str(k).upper(): float(v) for k, v in prices.items()}
        self.market_open = bool(market_open)
        self.holdings: dict[str, dict[str, float]] = {}
        self.orders: dict[str, Order] = {}
        self._seq = 0

    @property
    def mode(self) -> str:
        return "paper"

    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        return self.market_open

    def get_current_price(self, ticker: str) -> Optional[float]:
        return self.prices.get(str(ticker).upper())

    def get_holdings(self) -> list[Holding]:
        out: list[Holding] = []
        for ticker, pos in sorted(self.holdings.items()):
            shares = float(pos.get("shares", 0.0) or 0.0)
            if shares <= SHARE_EPS:
                continue
            avg_cost = float(pos.get("avg_cost", 0.0) or 0.0)
            cur = float(self.get_current_price(ticker) or avg_cost)
            market_value = shares * cur
            cost = shares * avg_cost
            pnl = market_value - cost
            pnl_pct = (pnl / cost * 100.0) if cost > 0 else 0.0
            out.append(Holding(ticker, shares, avg_cost, cur, market_value, pnl, pnl_pct))
        return out

    def get_balance(self) -> Balance:
        holdings = self.get_holdings()
        invested = sum(h.shares * h.avg_cost for h in holdings)
        market_value = sum(h.market_value for h in holdings)
        return Balance(
            cash_krw=self.cash,
            total_value_krw=self.cash + market_value,
            invested_krw=invested,
            holdings=holdings,
            fetched_at=datetime.now(KST).isoformat(),
        )

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"REHEARSAL-{self._seq:04d}"

    def _new_order(self, ticker: str, side: OrderSide, shares: float, order_type: OrderType, price: float, client_order_id: str) -> Order:
        return Order(
            order_id=self._next_order_id(),
            ticker=str(ticker).upper(),
            side=side,
            order_type=order_type,
            shares=round(float(shares or 0.0), 6),
            price=float(price or 0.0),
            status=OrderStatus.PENDING,
            submitted_at=datetime.now(KST).isoformat(),
            client_order_id=client_order_id,
        )

    def _store(self, order: Order) -> Order:
        self.orders[order.order_id] = order
        return order

    def place_buy(self, ticker: str, shares: float, order_type: OrderType = OrderType.MARKET, price: float = 0.0, client_order_id: str = "") -> Order:
        ticker = str(ticker).upper()
        order = self._new_order(ticker, OrderSide.BUY, shares, order_type, price, client_order_id)
        cur = float(price or self.get_current_price(ticker) or 0.0)
        if order.shares <= 0 or cur <= 0:
            order.status = OrderStatus.REJECTED
            order.message = "invalid shares/price"
            return self._store(order)
        notional = order.shares * cur
        if notional > self.cash + SHARE_EPS:
            order.status = OrderStatus.REJECTED
            order.message = f"insufficient cash: need {notional:.2f}, have {self.cash:.2f}"
            return self._store(order)
        prev = self.holdings.get(ticker, {"shares": 0.0, "avg_cost": 0.0})
        prev_shares = float(prev.get("shares", 0.0) or 0.0)
        prev_cost = float(prev.get("avg_cost", 0.0) or 0.0)
        new_shares = round(prev_shares + order.shares, 6)
        self.holdings[ticker] = {"shares": new_shares, "avg_cost": ((prev_shares * prev_cost) + notional) / new_shares}
        self.cash -= notional
        order.status = OrderStatus.FILLED
        order.filled_shares = order.shares
        order.filled_avg_price = cur
        order.filled_at = datetime.now(KST).isoformat()
        return self._store(order)

    def place_sell(self, ticker: str, shares: float, order_type: OrderType = OrderType.MARKET, price: float = 0.0, client_order_id: str = "") -> Order:
        ticker = str(ticker).upper()
        order = self._new_order(ticker, OrderSide.SELL, shares, order_type, price, client_order_id)
        cur = float(price or self.get_current_price(ticker) or 0.0)
        held = float(self.holdings.get(ticker, {}).get("shares", 0.0) or 0.0)
        if order.shares <= 0 or cur <= 0 or held + SHARE_EPS < order.shares:
            order.status = OrderStatus.REJECTED
            order.message = f"invalid sell: shares={order.shares:g} held={held:g} price={cur:g}"
            return self._store(order)
        remaining = round(held - order.shares, 6)
        if remaining <= SHARE_EPS:
            self.holdings.pop(ticker, None)
        else:
            self.holdings[ticker]["shares"] = remaining
        self.cash += order.shares * cur
        order.status = OrderStatus.FILLED
        order.filled_shares = order.shares
        order.filled_avg_price = cur
        order.filled_at = datetime.now(KST).isoformat()
        return self._store(order)

    def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(str(order_id))
        if order is None or order.status != OrderStatus.PENDING:
            return False
        order.status = OrderStatus.CANCELLED
        return True

    def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(str(order_id))


class OneShotBuyRuleBook:
    """Rulebook provider that emits exactly one BUY then HOLD."""

    def __init__(self, ticker: str) -> None:
        self.ticker = str(ticker).upper()
        self._count: dict[str, int] = {}
        self._rulebook = default_rulebook(self.ticker, asset_type="us_stock", direction="long")
        self._rulebook.exit_strategy = "hybrid"
        self._rulebook.stop_loss_atr = 2.0
        self._rulebook.take_profit_atr = 3.0
        self._rulebook.trailing_atr = 1.5
        self._rulebook.max_holding_days = 20
        self._rulebook.win_rate = 0.55
        self._rulebook.expectancy_pct = 1.2
        if hasattr(self._rulebook, "profit_factor"):
            self._rulebook.profit_factor = 1.4

    def name(self) -> str:
        return "OneShotBuyRuleBook(rehearsal)"

    def evaluate(self, ticker: str, price: float, df=None) -> SignalResult:
        ticker_u = str(ticker).upper()
        n = self._count.get(ticker_u, 0)
        self._count[ticker_u] = n + 1
        if n == 0:
            return SignalResult(
                ticker=ticker_u,
                signal=Signal.BUY,
                price=float(price),
                reason="rehearsal forced BUY",
                score=2.0,
                raw_score=2.0,
                threshold=2.0,
                market_adjustment=1.0,
                reasons=["rehearsal_forced_buy"],
            )
        return SignalResult(ticker=ticker_u, signal=Signal.HOLD, price=float(price), reason="rehearsal post-buy HOLD", threshold=2.0)

    def get_last_atr(self, ticker: str) -> float:
        return 2.0

    def get_rulebook(self, ticker: str) -> Rulebook:
        rb = Rulebook.from_dict(self._rulebook.to_dict())
        rb.ticker = str(ticker).upper()
        return rb

    def get_last_market_context(self, ticker: str):
        return SimpleNamespace(
            score=55.0,
            market_score=55.0,
            regime="neutral",
            vix_level=18.0,
            sector_strength={"tech": 50.0},
            sector_score=50.0,
            buy_multiplier=1.0,
        )


def _write_policy(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "small_amount_safety:",
                "  enabled: true",
                "  max_shares_per_order: 1000",
                "  max_notional_per_order: 1000000",
                "  max_bought_notional_per_day: 1000000",
                "  max_total_exposure_notional: 1000000",
                "  max_orders_per_day: 20",
                "  require_first_order_approval: false",
                "risk:",
                "  daily_loss_limit_pct: 99",
                "  consecutive_loss_limit: 99",
                "  cooldown_after_consecutive_loss_hours: 0",
                "entry:",
                "  one_position_per_symbol: true",
                "  cooldown_after_buy_hours: 0",
                "add_buy:",
                "  enabled: false",
                "  min_cooldown_minutes: 0",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _patch_state_paths(artifact_dir: Path) -> None:
    import engine.live.approval_manager as approval_mod
    import engine.live.pending_order_manager as pending_mod
    import engine.live.position_manager as position_mod
    import engine.live.safety.layer as safety_layer_mod
    import engine.live.safety.state as safety_state_mod

    position_mod.POSITIONS_PATH = artifact_dir / "positions.json"
    position_mod.TRADE_LOG_PATH = artifact_dir / "trade_log.csv"
    approval_mod.APPROVALS_PATH = artifact_dir / "approvals.json"
    pending_mod.PENDING_ORDERS_PATH = artifact_dir / "pending_orders.json"
    safety_state_mod.STATE_PATH = artifact_dir / "safety_state.json"
    safety_layer_mod.POSITIONS_PATH = artifact_dir / "positions.json"
    safety_layer_mod.KILL_SWITCH_PATH = artifact_dir / "KILL_SWITCH"
    safety_layer_mod.SYMBOLS_DIR = ROOT / "data" / "symbols"


def _positions_saved(path: Path, ticker: str, errors: list[str]) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ticker in payload and float(payload[ticker].get("shares", 0.0) or 0.0) > 0
    except Exception as exc:
        errors.append(f"positions parse failed: {exc}")
        return False


def run_rehearsal(
    *,
    ticker: str = "AAPL",
    price: float = 100.0,
    initial_cash: float = 100_000.0,
    order_notional: float = 30.0,
    artifact_dir: Optional[Path] = None,
    manual_sell_intent_path: Optional[Path] = None,
) -> RehearsalResult:
    ticker = str(ticker).upper().strip()
    if not ticker:
        raise ValueError("ticker required")
    if artifact_dir is None:
        artifact_dir = Path(tempfile.mkdtemp(prefix="kingmaker_live_rehearsal_"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _patch_state_paths(artifact_dir)
    policy_path = artifact_dir / "policy.yaml"
    _write_policy(policy_path)

    broker = RehearsalPaperBroker(initial_cash=initial_cash, prices={ticker: price}, market_open=True)
    notifier = RehearsalNotifier()
    safety = SafetyLayer(broker=broker, policy_path=policy_path)
    runner = Runner(
        broker=broker,
        safety=safety,
        notifier=notifier,
        clock=AlwaysOpenClock(),
        rulebook=OneShotBuyRuleBook(ticker),
        symbols=[ticker],
        order_shares=1.0,
        order_notional=float(order_notional),
        manual_sell_intent_path=manual_sell_intent_path,
    )

    errors: list[str] = []
    runner.startup_check()
    if runner.stats.last_error:
        errors.append(f"startup_check: {runner.stats.last_error}")

    runner.tick_market()
    if runner.stats.last_error and runner.stats.last_error not in errors:
        errors.append(f"tick_market: {runner.stats.last_error}")

    # daily_summary resets daily counters, so snapshot the market-tick stats first.
    market_stats = SimpleNamespace(
        signals_buy=runner.stats.signals_buy,
        signals_hold=runner.stats.signals_hold,
        orders_attempted=runner.stats.orders_attempted,
        orders_filled=runner.stats.orders_filled,
    )

    runner.daily_summary()
    if runner.stats.last_error and runner.stats.last_error not in errors:
        errors.append(f"daily_summary: {runner.stats.last_error}")

    positions_saved = _positions_saved(artifact_dir / "positions.json", ticker, errors)
    has_entry_alert = any(e.get("kind") == "trade_entry" for e in notifier.events)
    has_summary_alert = len([e for e in notifier.events if e.get("kind") == "send"]) >= 2

    if market_stats.signals_buy != 1:
        errors.append(f"expected 1 BUY signal, got {market_stats.signals_buy}")
    if market_stats.orders_attempted != 1:
        errors.append(f"expected 1 order attempt, got {market_stats.orders_attempted}")
    if market_stats.orders_filled != 1:
        errors.append(f"expected 1 filled order, got {market_stats.orders_filled}")
    if not broker.get_holdings():
        errors.append("broker holding was not created")
    if not positions_saved:
        errors.append("PositionManager did not save entry position")
    if not has_entry_alert:
        errors.append("entry notification was not emitted")
    if not has_summary_alert:
        errors.append("startup/daily summary notifications were not emitted")

    result = RehearsalResult(
        ok=not errors,
        artifact_dir=str(artifact_dir),
        ticker=ticker,
        orders_attempted=market_stats.orders_attempted,
        orders_filled=market_stats.orders_filled,
        signals_buy=market_stats.signals_buy,
        signals_hold=market_stats.signals_hold,
        broker_orders=len(broker.orders),
        broker_holdings=len(broker.get_holdings()),
        notifier_events=len(notifier.events),
        positions_saved=positions_saved,
        trade_log_exists=(artifact_dir / "trade_log.csv").exists(),
        errors=errors,
    )
    (artifact_dir / "rehearsal_result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "notifier_events.json").write_text(
        json.dumps(notifier.events, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kingmaker one-shot live dry-run rehearsal")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--price", type=float, default=100.0)
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--order-notional", type=float, default=30.0)
    parser.add_argument("--artifact-dir", default="", help="결과를 남길 디렉터리. 미지정 시 /tmp 아래 임시 디렉터리 사용")
    parser.add_argument("--cleanup", action="store_true", help="성공 후 artifact-dir 삭제. 실패 시 보존")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else None
    result = run_rehearsal(
        ticker=args.ticker,
        price=float(args.price),
        initial_cash=float(args.initial_cash),
        order_notional=float(args.order_notional),
        artifact_dir=artifact_dir,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if args.cleanup and result.ok:
        shutil.rmtree(result.artifact_dir, ignore_errors=True)
        print(f"[cleanup] removed {result.artifact_dir}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    raise SystemExit(main())
