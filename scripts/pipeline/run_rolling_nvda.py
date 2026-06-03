#!/usr/bin/env python3
"""Run one-ticker rolling validation smoke test for NVDA."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.rolling_validation import run_rolling_validation  # noqa: E402


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return _json_safe({k: v for k, v in vars(value).items() if not str(k).startswith("_")})
    return str(value)


def main() -> int:
    started = time.time()
    result = run_rolling_validation("NVDA")
    elapsed = time.time() - started

    run_id = result.get("run_id") or result.get("_meta", {}).get("run_id") or "unknown_run"
    out_dir = ROOT / "data/_system/pipeline/v1/runs" / str(run_id) / "NVDA"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "rolling_validation.json"
    out_path.write_text(json.dumps(_json_safe(result), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    score = result.get("stock_score", {})
    print("=" * 72)
    print("NVDA rolling validation smoke test")
    print("=" * 72)
    print(f"run_id:              {run_id}")
    print(f"elapsed_sec:         {elapsed:.2f}")
    print(f"data_range:          {result.get('data_start')} ~ {result.get('data_end')}")
    print(f"ADV 252d USD:        {float(result.get('adv_usd_252d', 0.0)):,.2f}")
    print(f"sentiment_days:      {result.get('sentiment_days')}")
    print()
    print("year | trades | win_rate | expectancy | profit_factor | mdd | pass")
    print("-----+--------+----------+------------+---------------+-----+------")
    for period in result.get("periods", []):
        oos = period.get("oos", {})
        year = period.get("year")
        score_period = next((p for p in score.get("periods", []) if p.get("year") == year), {})
        passed = score_period.get("pass")
        print(
            f"{year} | "
            f"{int(oos.get('trade_count', 0)):6d} | "
            f"{float(oos.get('win_rate', 0.0)):8.2f}% | "
            f"{float(oos.get('expectancy_pct', 0.0)):10.3f}% | "
            f"{float(oos.get('profit_factor', 0.0)):13.3f} | "
            f"{float(oos.get('max_drawdown_pct', 0.0)):5.2f}% | "
            f"{passed}"
        )
    print()
    print(f"consistency_score:   {score.get('consistency_score')}")
    print(f"quality_score:       {score.get('quality_score')} (provisional={score.get('quality_provisional')})")
    print(f"liquidity_weight:    {score.get('liquidity_weight')}")
    print(f"stock_score:         {score.get('stock_score')}")
    print(f"excluded:            {score.get('excluded')} {score.get('exclude_reason')}")
    print(f"saved:               {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
