from __future__ import annotations

from types import SimpleNamespace

from engine.live.broker import factory as factory_mod
from engine.live.exit_policy_guard import is_strict_live_broker, validate_startup_exit_policy


class FakeAlpaca:
    def __init__(self, paper=True):
        self.paper = paper
        self.mode = "alpaca_paper" if paper else "alpaca_live"


class FakePaper:
    mode = "paper"

    def __init__(self, initial_cash=0):
        self.initial_cash = initial_cash


def test_factory_supports_explicit_alpaca_live(monkeypatch):
    monkeypatch.setattr(factory_mod, "AlpacaBroker", FakeAlpaca)
    broker = factory_mod.make_broker(force_mode="alpaca_live")
    assert broker.mode == "alpaca_live"
    assert broker.paper is False

    broker2 = factory_mod.make_broker(force_mode="alpaca-live")
    assert broker2.mode == "alpaca_live"
    assert broker2.paper is False


def test_factory_keeps_alpaca_default_on_paper(monkeypatch):
    monkeypatch.setattr(factory_mod, "AlpacaBroker", FakeAlpaca)
    assert factory_mod.make_broker(force_mode="alpaca").mode == "alpaca_paper"
    assert factory_mod.make_broker(force_mode="alpaca_paper").mode == "alpaca_paper"


def test_alpaca_live_factory_output_triggers_exit_policy_fail_fast(monkeypatch):
    monkeypatch.setattr(factory_mod, "AlpacaBroker", FakeAlpaca)
    monkeypatch.delenv("EXIT_LIVE_POLICY", raising=False)
    monkeypatch.delenv("ALLOW_LEGACY_EXIT_LIVE", raising=False)
    broker = factory_mod.make_broker(force_mode="alpaca_live")
    assert is_strict_live_broker(broker) is True

    try:
        validate_startup_exit_policy(broker)
    except RuntimeError as exc:
        assert "EXIT_LIVE_POLICY=1" in str(exc)
    else:
        raise AssertionError("alpaca_live must fail fast without EXIT_LIVE_POLICY")

    monkeypatch.setenv("EXIT_LIVE_POLICY", "1")
    validate_startup_exit_policy(broker)


def test_paper_factory_unchanged(monkeypatch):
    monkeypatch.setattr(factory_mod, "CalendarAwarePaperBroker", FakePaper)
    broker = factory_mod.make_broker(force_mode="paper", paper_initial_cash=123)
    assert broker.mode == "paper"
    assert broker.initial_cash == 123
