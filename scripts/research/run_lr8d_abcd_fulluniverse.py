#!/usr/bin/env python3
"""LR-8D A+B+C+D full-universe runner.

This is a thin wrapper around the proven LR8C full-universe runner, but writes
into a fresh LR-8D output namespace so old LR8C rows cannot be resumed, skipped,
or mixed with the new A+B+C+D run.

Included by current imported pipeline code:
- A: conservative stress-average survivor gate
- B: expectancy-centered swing fitness + profit concentration penalty
- C: breakeven_enabled categorical search
- D: walk-forward sell_omen score merge + sell_omen exit
- buy-side ticker-news sentiment and topic features
- full trade dumps: rulebook_full, entry/exit_context_full, holding_path_full
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research import run_lr8c_run2_fulluniverse as runner

RUN_ID = "lr8d_abcd_20260608"
RUN_PREFIX = "lr8d_abcd"

runner.OUT_DIR = Path(f"data/_system/research/{RUN_ID}")
runner.TIMING_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_timing.txt"
runner.TOPN_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_topn.jsonl"
runner.RULEBOOKS_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_topn_rulebooks.jsonl"
runner.TRADES_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_trades.jsonl"
runner.SURVIVORS_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_survivors.jsonl"
runner.REPORT_PATH = runner.OUT_DIR / "LR8D_ABCD_REPORT.md"

_ORIGINAL_WRITE_REPORT = runner.write_survivors_and_report


def write_survivors_and_report(universe_symbols, timing):
    """Write standard survivor artifacts, then add a LR-8D run manifest."""
    _ORIGINAL_WRITE_REPORT(universe_symbols, timing)
    manifest = {
        "run_id": RUN_ID,
        "run_prefix": RUN_PREFIX,
        "branch_expected": "ml-sell-omen-20260608",
        "population": runner.POPULATION,
        "generations": runner.GENERATIONS,
        "balanced_k": runner.BALANCED_K,
        "strict_k": runner.STRICT_K,
        "general_years": list(runner.GENERAL_YEARS),
        "stress_label": runner.STRESS_LABEL,
        "features": {
            "A_survivor_stress_avg_gate": True,
            "B_expectancy_centered_fitness": True,
            "B_profit_concentration_penalty": True,
            "C_breakeven_enabled_categorical": True,
            "D_sell_omen_walk_forward_merge": True,
            "buy_news_global_and_topic_features": True,
            "full_trade_dump": True,
        },
        "paths": {
            "out_dir": str(runner.OUT_DIR),
            "topn": str(runner.TOPN_PATH),
            "rulebooks": str(runner.RULEBOOKS_PATH),
            "trades": str(runner.TRADES_PATH),
            "survivors": str(runner.SURVIVORS_PATH),
            "report": str(runner.REPORT_PATH),
            "timing": str(runner.TIMING_PATH),
        },
    }
    manifest_path = runner.OUT_DIR / "LR8D_ABCD_MANIFEST.json"
    manifest_path.write_text(runner.json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


runner.write_survivors_and_report = write_survivors_and_report


def main() -> int:
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
