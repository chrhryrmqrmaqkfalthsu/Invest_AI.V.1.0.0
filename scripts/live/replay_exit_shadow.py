#!/usr/bin/env python3
"""C-P1 replay verification for the live ExitPolicy shadow.

The completed au_1173 trades predate BB rulebook/entry-context recording, so
this replay cannot reconstruct the exact historical live position. Instead it
feeds observed trade exit snapshots through a fixed, documented scenario
matrix. Both legacy live polling and ExitPolicy see the exact same snapshot.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.exit_policy import MarketContext as ExitMarketContext, initialize_position_state  # noqa: E402
from engine.live.exit_policy_adapter import evaluate_live_shadow, legacy_live_decision  # noqa: E402
from engine.live.position_manager import PositionEntry  # noqa: E402
from engine.strategies.rulebook import Rulebook  # noqa: E402

RUNS_ROOT = ROOT / "data/_system/pipeline/v1/runs"
ANALYSIS_ROOT = ROOT / "data/_system/pipeline/v1/analysis/c_p1_shadow"
DEFAULT_RUN_ID = "au_1173_20260604"
REPLAY_WARNING = (
    "C-P1 replay 근사: au_1173 거래에는 당시 rulebook_snapshot/entry ATR/stop/target/trailing이 없다. "
    "관측된 PnL·보유 거래일·달력일을 고정 시나리오에 주입해 기계적 정합성 차이만 측정한다."
)

SCENARIOS = (
    {"name": "fixed_neutral", "exit_strategy": "fixed", "market_score": 50.0, "vix_level": 18.0},
    {"name": "trailing_neutral", "exit_strategy": "trailing", "market_score": 50.0, "vix_level": 18.0},
    {"name": "hybrid_neutral", "exit_strategy": "hybrid", "market_score": 50.0, "vix_level": 18.0},
    {"name": "hybrid_bear", "exit_strategy": "hybrid", "market_score": 30.0, "vix_level": 18.0},
    {"name": "hybrid_bull_volatile", "exit_strategy": "hybrid", "market_score": 80.0, "vix_level": 30.0},
)


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def collect_trade_snapshots(run_id: str, max_trades: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_dir = RUNS_ROOT / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"run directory not found: {run_dir}")
    snapshots: list[dict[str, Any]] = []
    skipped_files = 0
    files = sorted(run_dir.glob("*/rolling_validation.json"))
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            skipped_files += 1
            continue
        ticker = path.parent.name.upper()
        for period in data.get("periods") or []:
            for trade in period.get("trades") or []:
                if not isinstance(trade, dict):
                    continue
                pnl_pct = safe_float(trade.get("pnl_pct"))
                entry_date = str(trade.get("entry_date") or "")
                exit_date = str(trade.get("exit_date") or "")
                if pnl_pct is None or not entry_date or not exit_date:
                    continue
                try:
                    calendar_days = max(0, (datetime.fromisoformat(exit_date) - datetime.fromisoformat(entry_date)).days)
                except Exception:
                    calendar_days = max(0, safe_int(trade.get("holding_days"), 0))
                snapshots.append({
                    "ticker": ticker,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "observed_exit_reason": str(trade.get("exit_reason") or "unknown"),
                    "pnl_pct": pnl_pct,
                    "holding_trading_days": max(0, safe_int(trade.get("holding_days"), 0)),
                    "holding_calendar_days": calendar_days,
                })
                if max_trades is not None and len(snapshots) >= max(0, int(max_trades)):
                    return snapshots, {"run_id": run_id, "rolling_file_count": len(files), "skipped_file_count": skipped_files, "source_trade_count": len(snapshots), "limited": True}
    return snapshots, {"run_id": run_id, "rolling_file_count": len(files), "skipped_file_count": skipped_files, "source_trade_count": len(snapshots), "limited": False}


def build_replay_rulebook(ticker: str, scenario: dict[str, Any]) -> Rulebook:
    return Rulebook(
        ticker=ticker, asset_type="us_stock", direction="long", exit_strategy=str(scenario["exit_strategy"]),
        stop_loss_atr=2.0, take_profit_atr=3.0, trailing_atr=1.5, max_holding_days=20,
        stop_loss_atr_bear=1.0, take_profit_atr_bull=5.0, trailing_atr_volatile=3.0, sector_name="tech",
    )


def build_replay_position(snapshot: dict[str, Any], rulebook: Rulebook, current_price: float) -> PositionEntry:
    base = initialize_position_state(
        ticker=snapshot["ticker"], entry_price=100.0, shares=1.0, rulebook=rulebook, atr_value=2.0,
        market_context=ExitMarketContext(market_score=50.0, vix_level=18.0, sector_score=50.0),
        entry_date=snapshot["entry_date"],
    )
    highest = max(100.0, float(current_price))
    lowest = min(100.0, float(current_price))
    return PositionEntry(
        ticker=snapshot["ticker"], entry_date=snapshot["entry_date"], entry_price=100.0, shares=1.0,
        atr_at_entry=2.0, stop_price=base.stop_price, target_price=base.target_price,
        trailing_distance=base.trailing_distance, trailing_stop=max(base.trailing_stop, highest - base.trailing_distance),
        highest_price=highest, lowest_price=lowest, exit_strategy=rulebook.exit_strategy, max_holding_days=rulebook.max_holding_days,
        rulebook_direction="long",
    )


def replay_one(snapshot: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    rulebook = build_replay_rulebook(snapshot["ticker"], scenario)
    current_price = max(0.01, 100.0 * (1.0 + float(snapshot["pnl_pct"]) / 100.0))
    pos = build_replay_position(snapshot, rulebook, current_price)
    legacy = legacy_live_decision(pos, current_price, snapshot["holding_calendar_days"])
    raw_context = SimpleNamespace(score=float(scenario["market_score"]), vix_level=float(scenario["vix_level"]), sector_strength={"tech": 50.0})
    record = evaluate_live_shadow(
        ticker=snapshot["ticker"], pos=pos, price=current_price, rulebook=rulebook, raw_market_context=raw_context,
        holding_calendar_days=snapshot["holding_calendar_days"], holding_trading_days=snapshot["holding_trading_days"],
        actual_legacy_reason=legacy.get("reason"), rulebook_source="C-P1 replay fixed scenario rulebook", timestamp=snapshot["exit_date"],
    )
    record["replay"] = {
        "scenario": scenario["name"], "observed_exit_reason": snapshot["observed_exit_reason"],
        "observed_pnl_pct": snapshot["pnl_pct"], "normalized_entry_price": 100.0,
        "normalized_current_price": current_price, "source_holding_trading_days": snapshot["holding_trading_days"],
        "source_holding_calendar_days": snapshot["holding_calendar_days"],
    }
    return record


def replay_snapshots(snapshots: Iterable[dict[str, Any]], scenarios: Iterable[dict[str, Any]] = SCENARIOS, example_limit: int = 5) -> dict[str, Any]:
    type_counts: Counter[str] = Counter()
    scenario_counts: dict[str, Counter[str]] = defaultdict(Counter)
    observed_reason_counts: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evaluation_count = 0
    for snapshot in snapshots:
        for scenario in scenarios:
            record = replay_one(snapshot, scenario)
            diff_type = str(record["difference_type"])
            evaluation_count += 1
            type_counts[diff_type] += 1
            scenario_counts[str(scenario["name"])][diff_type] += 1
            observed_reason_counts[str(snapshot["observed_exit_reason"])][diff_type] += 1
            if len(examples[diff_type]) < max(0, int(example_limit)):
                examples[diff_type].append(record)
    mismatch_count = evaluation_count - type_counts.get("SAME", 0)
    return {
        "evaluation_count": evaluation_count,
        "same_count": type_counts.get("SAME", 0),
        "different_count": mismatch_count,
        "different_rate_pct": mismatch_count / evaluation_count * 100.0 if evaluation_count else 0.0,
        "difference_type_counts": dict(type_counts),
        "difference_type_rates_pct": {key: value / evaluation_count * 100.0 if evaluation_count else 0.0 for key, value in type_counts.items()},
        "by_scenario": {name: dict(counts) for name, counts in scenario_counts.items()},
        "by_observed_exit_reason": {name: dict(counts) for name, counts in observed_reason_counts.items()},
        "examples": dict(examples),
    }


def render_text(payload: dict[str, Any]) -> str:
    result = payload["result"]
    lines = [
        "=" * 112, "C-P1 Live ExitPolicy Shadow Replay Verification", "=" * 112,
        f"WARNING: {payload['warning']}", f"generated_at: {payload['generated_at']}",
        f"run_id: {payload['source']['run_id']}", f"source_trades: {payload['source']['source_trade_count']}",
        f"scenarios: {len(payload['scenarios'])}", f"evaluations: {result['evaluation_count']}",
        f"same: {result['same_count']}", f"different: {result['different_count']} ({result['different_rate_pct']:.3f}%)", "", "Difference types", "-" * 80,
    ]
    for key, count in sorted(result["difference_type_counts"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"{key:38s} {count:9d}  {result['difference_type_rates_pct'].get(key, 0.0):8.3f}%")
    lines += ["", "By scenario", "-" * 80]
    for scenario, counts in result["by_scenario"].items():
        total = sum(counts.values()); different = total - counts.get("SAME", 0)
        lines.append(f"{scenario:28s} total={total:8d} different={different:8d} ({(different/total*100 if total else 0):7.3f}%) {counts}")
    lines += ["", f"BUG_CANDIDATE: {result['difference_type_counts'].get('BUG_CANDIDATE', 0)}", "Cutover 판단은 실제 live paper shadow와 BB 이후 데이터로 재검증해야 한다."]
    return "\n".join(lines)


def save_outputs(payload: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "replay_exit_shadow.json"; txt_path = out_dir / "replay_exit_shadow.txt"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(render_text(payload), encoding="utf-8")
    return json_path, txt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay live legacy exit vs ExitPolicy using completed trade snapshots.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID); parser.add_argument("--out", default=str(ANALYSIS_ROOT))
    parser.add_argument("--max-trades", type=int); parser.add_argument("--example-limit", type=int, default=5)
    args = parser.parse_args(argv)
    started = time.time(); snapshots, source_meta = collect_trade_snapshots(args.run_id, max_trades=args.max_trades)
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "analysis_label": "C-P1 replay verification (approximate/mechanical)", "warning": REPLAY_WARNING, "source": source_meta, "scenarios": list(SCENARIOS), "result": replay_snapshots(snapshots, example_limit=args.example_limit), "elapsed_sec": time.time() - started}
    json_path, txt_path = save_outputs(payload, Path(args.out)); print(render_text(payload)); print(f"\nelapsed_sec: {payload['elapsed_sec']:.2f}"); print(f"json_out: {json_path}"); print(f"txt_out:  {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
