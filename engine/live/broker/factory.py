"""
BrokerFactory - 환경설정에 따라 Paper / KIS / Alpaca broker 자동 선택.

Live factory 경로는 calendar-aware PaperBroker와 US fail-closed KIS wrapper를
사용한다. AlpacaBroker는 자체 clock/API를 그대로 사용한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from dotenv import dotenv_values

from .base import Broker
from .market_aware import CalendarAwarePaperBroker, GuardedKisBroker
from .alpaca import AlpacaBroker

ENV_PATH = Path.home() / "kingmaker" / ".env"


def make_broker(
    force_mode: Optional[str] = None,
    dry_run: bool = False,
    paper_initial_cash: float = 1_000_000,
) -> Broker:
    """Create the configured broker without exposing credentials."""
    env = dotenv_values(str(ENV_PATH))
    raw_mode = force_mode or env.get("BROKER_MODE") or env.get("KIS_MODE", "paper")
    mode = str(raw_mode).strip().lower()

    if mode == "paper":
        return CalendarAwarePaperBroker(initial_cash=paper_initial_cash)
    if mode in ("real", "live", "vts"):
        # KIS constructor reads its own .env values; wrapper only adds fail-closed guards.
        return GuardedKisBroker(dry_run=dry_run)
    if mode in ("alpaca", "alpaca_paper", "alpaca-paper"):
        return AlpacaBroker(paper=True)
    raise ValueError(
        f"알 수 없는 mode: {mode!r}. paper / real / vts / live / alpaca_paper 중 하나여야 함."
    )


if __name__ == "__main__":
    print("BrokerFactory loaded. Live wrappers: CalendarAwarePaperBroker / GuardedKisBroker")
