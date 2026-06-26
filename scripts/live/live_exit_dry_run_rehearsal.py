#!/usr/bin/env python3
"""One-shot live exit dry-run rehearsal for Kingmaker.

RUN/real state safe: each scenario uses an isolated temporary state directory,
the in-memory rehearsal paper broker, and a Telegram capture sink.

Covered path per scenario:
    entry BUY rehearsal -> position mutation -> runner.tick_market
    -> PositionManager.check_exits -> broker SELL fill -> trade_log append
    -> Runner._record_realized_pnl_from_trade -> SafetyLayer.record_realized_pnl
    -> exit notification capture

Scenarios: stop_loss, take_profit, trailing, breakeven_stop, sell_omen, time_out.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.runner import Runner  # noqa: E402
from engine.live.safety.layer import SafetyLayer  # noqa: E402
from scripts.live.live_dry_run_rehearsal import (  # noqa: E402
    AlwaysOpenClock,
    OneShotBuyRuleBook,
    RehearsalNotifier,
    RehearsalPaperBroker,
    _patch_state_paths,
    _write_policy,
)

TICKER = "AAPL"
ENTRY_PRICE = 100.0
INITIAL_CASH = 100_000.0
ORDER_NOTIONAL = 100.0


@dataclass(frozen=True)
class ExitScenario:
    name: str
    expected_reason: str
    exit_price: float
    old_entry_days: int = 1
    exit_strategy: str = "hybrid"
    stop_price: float = 90.0
    target_price: float = 150.0
    trailing_distance: float = 5.0
    trailing_stop: float = 95.0
    highest_price: float = 100.0
    max_holding_days: int = 20
    breakeven_enabled: bool = False
    breakeven_trigger_profit_pct: float = 4.0
    breakeven_floor_profit_pct: float = 0.5
    sell_omen_enabled: bool = False
    sell_omen_threshold: float = 0.7
    sell_omen_score: Optional[float] = None


SCENARIOS: tuple[ExitScenario, ...] = (
    ExitScenario("stop_loss", "stop_loss", 94.0, stop_price=95.0, target_price=150.0),
    ExitScenario("take_profit", "take_profit", 106.5, stop_price=90.0, target_price=106.0),
    ExitScenario(
        "trailing", "trailing", 107.0,
        old_entry_days=7, stop_price=90.0, target_price=150.0,
        highest_price=112.0, trailing_distance=4.0, trailing_stop=108.0,
    ),
    ExitScenario(
        "breakeven_stop", "breakeven_stop", 100.4,
        old_entry_days=7, stop_price=90.0, target_price=150.0,
        highest_price=106.0, breakeven_enabled=True,
        breakeven_trigger_profit_pct=4.0, breakeven_floor_profit_pct=0.5,
    ),
    ExitScenario(
        "sell_omen", "sell_omen", 101.0,
        stop_price=90.0, target_price=150.0, highest_price=101.0,
        sell_omen_enabled=True, sell_omen_threshold=0.7, sell_omen_score=0.9,
    ),
    ExitScenario(
        "time_out", "time_out", 101.0,
        old_entry_days=30, stop_price=90.0, target_price=150.0,
        highest_price=101.0, max_holding_days=1,
    ),
)


@dataclass
class ExitScenarioResult:
    scenario: str
    ok: bool
    expected_reason: str
    exit_reason: str
    pnl_krw: float
    pnl_pct: float
    realized_pnl_today: float
    consecutive_losses: int
    cooldown_until: str
    kill_until: str
    position_removed: bool
    trade_log_written: bool
    exit_notification_sent: bool
    orders_attempted_before_exit: int
    orders_filled_before_exit: int
    orders_attempted_after_exit: int
    orders_filled_after_exit: int
    broker_orders: int
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class ExitRehearsalResult:
    ok: bool
    artifact_dir: str
    scenario_count: int
    passed: int
    failed: int
    scenarios: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _read_trade_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class PatchStack:
    def __init__(self, *, sell_omen_score: Optional[float]) -> None:
        self.sell_omen_score = sell_omen_score
        self.old_exit_policy = os.environ.get("EXIT_LIVE_POLICY")
        self._originals: list[tuple[Any, str, Any]] = []

    def _patch_attr(self, module: Any, name: str, value: Any) -> None:
        self._originals.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    def __enter__(self):
        os.environ["EXIT_LIVE_POLICY"] = "1"
        import engine.live.exit_policy_adapter as adapter
        import engine.live.news_alerts as news_alerts
        import engine.market.context as market_context

        original_sell_omen = adapter._live_sell_omen_kwargs

        def forced_sell_omen_kwargs(ticker: str, rulebook: Any, timestamp: Optional[str]) -> dict[str, Any]:
            base = original_sell_omen(ticker, rulebook, timestamp)
            if self.sell_omen_score is not None:
                base["sell_omen_score"] = float(self.sell_omen_score)
            return base

        neutral_ctx = SimpleNamespace(
            score=55.0,
            market_score=55.0,
            regime="neutral",
            vix_level=18.0,
            sector_strength={"tech": 50.0},
            sector_score=50.0,
            buy_multiplier=1.0,
        )
        self._patch_attr(adapter, "_live_sell_omen_kwargs", forced_sell_omen_kwargs)
        self._patch_attr(news_alerts, "maybe_send_sell_omen_prealert", lambda *args, **kwargs: None)
        self._patch_attr(market_context, "get_market_context", lambda *args, **kwargs: neutral_ctx)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for module, name, value in reversed(self._originals):
            setattr(module, name, value)
        if self.old_exit_policy is None:
            os.environ.pop("EXIT_LIVE_POLICY", None)
        else:
            os.environ["EXIT_LIVE_POLICY"] = self.old_exit_policy


def _make_runner(
    artifact_dir: Path,
    *,
    manual_sell_intent_path: Optional[Path] = None,
) -> tuple[Runner, RehearsalPaperBroker, RehearsalNotifier]:
    _patch_state_paths(artifact_dir)
    policy_path = artifact_dir / "policy.yaml"
    _write_policy(policy_path)
    broker = RehearsalPaperBroker(initial_cash=INITIAL_CASH, prices={TICKER: ENTRY_PRICE}, market_open=True)
    notifier = RehearsalNotifier()
    safety = SafetyLayer(broker=broker, policy_path=policy_path)
    runner = Runner(
        broker=broker,
        safety=safety,
        notifier=notifier,
        clock=AlwaysOpenClock(),
        rulebook=OneShotBuyRuleBook(TICKER),
        symbols=[TICKER],
        order_shares=1.0,
        order_notional=ORDER_NOTIONAL,
        manual_sell_intent_path=manual_sell_intent_path,
    )
    return runner, broker, notifier


def _enter_position(runner: Runner) -> list[str]:
    errors: list[str] = []
    runner.startup_check()
    if runner.stats.last_error:
        errors.append(f"startup_check: {runner.stats.last_error}")
    runner.tick_market()
    if runner.stats.last_error and runner.stats.last_error not in errors:
        errors.append(f"entry tick_market: {runner.stats.last_error}")
    if runner.position_manager.get(TICKER) is None:
        errors.append("entry did not create PositionEntry")
    return errors


def _mutate_position_for_scenario(runner: Runner, scenario: ExitScenario) -> None:
    pos = runner.position_manager.get(TICKER)
    if pos is None:
        raise RuntimeError("PositionEntry missing before scenario mutation")
    rb = dict(pos.rulebook_snapshot or {})
    rb.update(
        {
            "ticker": TICKER,
            "direction": "long",
            "exit_strategy": scenario.exit_strategy,
            "trailing_activation_profit_pct": 0.0,
            "breakeven_enabled": scenario.breakeven_enabled,
            "breakeven_trigger_profit_pct": scenario.breakeven_trigger_profit_pct,
            "breakeven_floor_profit_pct": scenario.breakeven_floor_profit_pct,
            "sell_omen_enabled": scenario.sell_omen_enabled,
            "sell_omen_threshold": scenario.sell_omen_threshold,
            "max_holding_days": scenario.max_holding_days,
        }
    )
    pos.rulebook_snapshot = rb
    pos.entry_date = (datetime.now().astimezone() - timedelta(days=scenario.old_entry_days)).isoformat()
    pos.entry_price = ENTRY_PRICE
    pos.atr_at_entry = 2.0
    pos.stop_price = scenario.stop_price
    pos.target_price = scenario.target_price
    pos.trailing_distance = scenario.trailing_distance
    pos.trailing_stop = scenario.trailing_stop
    pos.highest_price = scenario.highest_price
    pos.exit_strategy = scenario.exit_strategy
    pos.max_holding_days = scenario.max_holding_days
    pos.rulebook_direction = "long"
    runner.position_manager._save()


def _position_removed(scenario_dir: Path, runner: Runner) -> bool:
    if runner.position_manager.get(TICKER) is not None:
        return False
    return TICKER not in _read_json(scenario_dir / "positions.json")


def _failure_result(scenario: ExitScenario, scenario_dir: Path, runner: Runner, broker: RehearsalPaperBroker, notifier: RehearsalNotifier, errors: list[str]) -> ExitScenarioResult:
    state = _read_json(scenario_dir / "safety_state.json")
    return ExitScenarioResult(
        scenario=scenario.name,
        ok=False,
        expected_reason=scenario.expected_reason,
        exit_reason="",
        pnl_krw=0.0,
        pnl_pct=0.0,
        realized_pnl_today=_to_float(state.get("realized_pnl_today"), 0.0),
        consecutive_losses=int(_to_float(state.get("consecutive_losses"), 0.0)),
        cooldown_until=str(state.get("cooldown_until", "") or ""),
        kill_until=str(state.get("kill_until", "") or ""),
        position_removed=False,
        trade_log_written=(scenario_dir / "trade_log.csv").exists(),
        exit_notification_sent=any(e.get("kind") == "trade_exit" for e in notifier.events),
        orders_attempted_before_exit=int(runner.stats.orders_attempted),
        orders_filled_before_exit=int(runner.stats.orders_filled),
        orders_attempted_after_exit=int(runner.stats.orders_attempted),
        orders_filled_after_exit=int(runner.stats.orders_filled),
        broker_orders=len(broker.orders),
        errors=errors,
    )


def _run_one_scenario(
    scenario: ExitScenario,
    scenario_dir: Path,
    *,
    manual_sell_intent_path: Optional[Path] = None,
) -> ExitScenarioResult:
    scenario_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with PatchStack(sell_omen_score=scenario.sell_omen_score):
        runner, broker, notifier = _make_runner(
            scenario_dir,
            manual_sell_intent_path=manual_sell_intent_path,
        )
        errors.extend(_enter_position(runner))
        if errors:
            return _failure_result(scenario, scenario_dir, runner, broker, notifier, errors)

        before_attempted = int(runner.stats.orders_attempted)
        before_filled = int(runner.stats.orders_filled)
        _mutate_position_for_scenario(runner, scenario)
        broker.prices[TICKER] = float(scenario.exit_price)
        runner.tick_market()
        if runner.stats.last_error and runner.stats.last_error not in errors:
            errors.append(f"exit tick_market: {runner.stats.last_error}")

        trades = _read_trade_log(scenario_dir / "trade_log.csv")
        trade = trades[-1] if trades else {}
        exit_reason = str(trade.get("exit_reason") or "")
        pnl_krw = _to_float(trade.get("pnl_krw"), 0.0)
        pnl_pct = _to_float(trade.get("pnl_pct"), 0.0)
        state = _read_json(scenario_dir / "safety_state.json")
        realized = _to_float(state.get("realized_pnl_today"), 0.0)
        actual_losses = int(_to_float(state.get("consecutive_losses"), 0.0))
        removed = _position_removed(scenario_dir, runner)
        exit_alert = any(e.get("kind") == "trade_exit" for e in notifier.events)

        if exit_reason != scenario.expected_reason:
            errors.append(f"expected exit_reason={scenario.expected_reason}, got {exit_reason or 'NONE'}")
        if not trades:
            errors.append("trade_log was not written")
        if not removed:
            errors.append("position was not removed after filled SELL")
        if not exit_alert:
            errors.append("exit notification was not captured")
        if abs(realized - pnl_krw) > 1e-6:
            errors.append(f"SafetyLayer realized_pnl_today mismatch: expected {pnl_krw}, got {realized}")
        expected_losses = 1 if pnl_krw < 0 else 0
        if actual_losses != expected_losses:
            errors.append(f"consecutive_losses mismatch: expected {expected_losses}, got {actual_losses}")

        return ExitScenarioResult(
            scenario=scenario.name,
            ok=not errors,
            expected_reason=scenario.expected_reason,
            exit_reason=exit_reason,
            pnl_krw=pnl_krw,
            pnl_pct=pnl_pct,
            realized_pnl_today=realized,
            consecutive_losses=actual_losses,
            cooldown_until=str(state.get("cooldown_until", "") or ""),
            kill_until=str(state.get("kill_until", "") or ""),
            position_removed=removed,
            trade_log_written=bool(trades),
            exit_notification_sent=exit_alert,
            orders_attempted_before_exit=before_attempted,
            orders_filled_before_exit=before_filled,
            orders_attempted_after_exit=int(runner.stats.orders_attempted),
            orders_filled_after_exit=int(runner.stats.orders_filled),
            broker_orders=len(broker.orders),
            errors=errors,
        )


def run_exit_rehearsal(
    *,
    artifact_dir: Optional[Path] = None,
    scenarios: tuple[ExitScenario, ...] = SCENARIOS,
    manual_sell_intent_path: Optional[Path] = None,
) -> ExitRehearsalResult:
    if artifact_dir is None:
        artifact_dir = Path(tempfile.mkdtemp(prefix="kingmaker_live_exit_rehearsal_"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    results: list[ExitScenarioResult] = []
    for scenario in scenarios:
        result = _run_one_scenario(
            scenario,
            artifact_dir / scenario.name,
            manual_sell_intent_path=manual_sell_intent_path,
        )
        results.append(result)
        (artifact_dir / scenario.name / "scenario_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    payload = ExitRehearsalResult(
        ok=all(r.ok for r in results),
        artifact_dir=str(artifact_dir),
        scenario_count=len(results),
        passed=sum(1 for r in results if r.ok),
        failed=sum(1 for r in results if not r.ok),
        scenarios=[r.to_dict() for r in results],
    )
    (artifact_dir / "exit_rehearsal_result.json").write_text(
        json.dumps(payload.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kingmaker live exit dry-run rehearsal")
    parser.add_argument("--artifact-dir", default="", help="결과를 남길 디렉터리. 미지정 시 /tmp 아래 임시 디렉터리 사용")
    parser.add_argument("--cleanup", action="store_true", help="성공 후 artifact-dir 삭제. 실패 시 보존")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    result = run_exit_rehearsal(artifact_dir=Path(args.artifact_dir) if args.artifact_dir else None)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    if args.cleanup and result.ok:
        shutil.rmtree(result.artifact_dir, ignore_errors=True)
        print(f"[cleanup] removed {result.artifact_dir}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
