from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from engine.live import central_control as cc
from engine.live.buy_reconciliation import BuyReconciliationService
from engine.live.central_control import LiveCentralControlConfig, LiveCentralController


class Stats:
    def __init__(self):
        self.market_ticks = 1
        self.signals_buy = 0
        self.signals_sell = 0
        self.signals_hold = 0
        self.orders_attempted = 0
        self.orders_blocked = 0


class Broker:
    def get_current_price(self, ticker):
        return 100.0

    def get_holdings(self):
        return []


class PositionManager:
    load_error = ""

    def all(self):
        return []


class PendingManager:
    load_error = ""

    def all(self):
        return []

    def is_ticker_locked(self, ticker):
        return False


class RulebookProvider:
    def __init__(self):
        self._last_atr = {}
        self._rulebook_by_ticker = {}
        self._last_market_context = {}

    def _get_ohlcv(self, ticker):
        return pd.DataFrame({"ATR": [1.0] * 60, "Close": [100.0] * 60})

    def _signal_date(self, df):
        return pd.Timestamp("2026-06-26")

    def _lookup_lagged_news_context(self, ticker, rb, signal_date):
        return 0.0, {}, "test"


class Runner:
    def __init__(self):
        self.stats = Stats()
        self.broker = Broker()
        self.position_manager = PositionManager()
        self.pending_order_manager = PendingManager()
        self.rulebook = RulebookProvider()
        self.order_notional = 30.0
        self.orders = []

    def _maybe_reconfirm_existing(self, ticker, price):
        return None

    def _try_order(self, side, ticker, price, reason, signal_result=None, rulebook_override=None):
        self.stats.orders_attempted += 1
        self.orders.append(
            {
                "side": side,
                "ticker": ticker,
                "price": price,
                "reason": reason,
                "signal_result": signal_result,
                "rulebook_override": rulebook_override,
                "order_notional": self.order_notional,
            }
        )


def _rulebook(ticker: str, stop_loss_atr: float, take_profit_atr: float) -> dict:
    return {
        "ticker": ticker,
        "asset_type": "us_stock",
        "direction": "long",
        "version": "v5",
        "signal_threshold": 1.0,
        "stop_loss_atr": stop_loss_atr,
        "take_profit_atr": take_profit_atr,
        "trailing_atr": 1.0,
        "max_holding_days": 10,
        "exit_strategy": "fixed",
        "fitness": 10.0 + stop_loss_atr,
        "win_rate": 60.0,
        "trade_count": 20,
    }


def _controller(stage3_enabled: bool = True):
    ctl = LiveCentralController.__new__(LiveCentralController)
    ctl.runner = Runner()
    ctl.config = LiveCentralControlConfig(
        enabled=True,
        buy_mode="auto",
        max_positions=8,
        stage3_mix_enabled=stage3_enabled,
        central_stage3_min_confidence=0.0,
    )
    ctl.selection_metric = "confidence"
    ctl.position_sizing = "score_weighted"
    ctl.confidence_mode = "adjusted"
    ctl.buy_mode = "auto"
    ctl.selection_scores = {}
    ctl._is_live_whitelisted_ticker = lambda ticker: True
    ctl._same_day_reentry_blocked_tickers = lambda trade_date: set()
    ctl._trade_date_et = lambda: "2026-06-26"
    return ctl


def test_buy_reconciliation_preflight_uses_override_without_ticker_fallback():
    class ProviderNoFallback:
        def __init__(self):
            self.get_rulebook_called = False

        def get_last_atr(self, ticker):
            return 1.0

        def get_rulebook(self, ticker):
            self.get_rulebook_called = True
            raise AssertionError("ticker-scoped fallback should not be called when override exists")

    provider = ProviderNoFallback()
    svc = BuyReconciliationService(
        broker=SimpleNamespace(),
        rulebook_provider=provider,
        position_manager=SimpleNamespace(),
        pending_manager=SimpleNamespace(),
    )
    override = _rulebook("AAA", stop_loss_atr=1.7, take_profit_atr=3.1)

    preflight = svc.preflight("AAA", rulebook_override=override)

    assert provider.get_rulebook_called is False
    assert preflight.rulebook.stop_loss_atr == 1.7
    assert preflight.rulebook.take_profit_atr == 3.1


def test_stage2_and_stage3_candidates_use_their_selected_entity_rulebook(monkeypatch):
    ctl = _controller(stage3_enabled=True)
    stage2_rb = _rulebook("AAA", stop_loss_atr=1.1, take_profit_atr=2.2)
    stage3_rb = _rulebook("BBB", stop_loss_atr=3.3, take_profit_atr=4.4)
    ctl.entity_by_ticker = {
        "AAA": [SimpleNamespace(entity_id="AAA_stage2", ticker="AAA", confidence=2.0, rulebook=stage2_rb, tags={"stage": "stage2"})],
        "BBB": [SimpleNamespace(entity_id="BBB_stage3", ticker="BBB", confidence=2.0, rulebook=stage3_rb, tags={"stage": "stage3_live_pool"})],
    }
    evaluated = {}

    def fake_evaluate_signal(*, rb, df, market_score, sector_score, vix_level, news_sentiment, event_flags, topic_features):
        evaluated[rb.ticker] = rb.stop_loss_atr
        return SimpleNamespace(
            should_buy=True,
            score=5.0,
            raw_score=5.0,
            threshold=1.0,
            market_adjustment=1.0,
            reasons=["test"],
        )

    monkeypatch.setattr(cc, "evaluate_signal", fake_evaluate_signal)
    monkeypatch.setattr(cc, "get_market_context", lambda: None)

    ctl._process_central_buy_selection()

    by_ticker = {order["ticker"]: order for order in ctl.runner.orders}
    assert evaluated == {"AAA": 1.1, "BBB": 3.3}
    assert by_ticker["AAA"]["rulebook_override"]["stop_loss_atr"] == 1.1
    assert by_ticker["BBB"]["rulebook_override"]["stop_loss_atr"] == 3.3
    assert ctl.runner.rulebook._rulebook_by_ticker == {"BBB": cc.Rulebook.from_dict(stage3_rb)}
    assert "AAA" not in ctl.runner.rulebook._rulebook_by_ticker
