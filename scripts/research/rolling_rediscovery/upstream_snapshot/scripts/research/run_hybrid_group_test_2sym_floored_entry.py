#!/usr/bin/env python3
"""State-safe importlib entrypoint for the floored AAP/POWI hybrid test."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any


class _NoopLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        return None

    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None

    def success(self, *args: Any, **kwargs: Any) -> None:
        return None

    def bind(self, *args: Any, **kwargs: Any) -> "_NoopLogger":
        return self


logger_stub = types.ModuleType("engine.core.logger")
logger_stub.get_logger = lambda name="": _NoopLogger()
logger_stub.trade_logger = lambda: _NoopLogger()
sys.modules["engine.core.logger"] = logger_stub

HERE = Path(__file__).resolve().parent
ISOLATED_ROOT = HERE.parents[1]
KINGMAKER_ROOT = HERE.parents[5]
if str(ISOLATED_ROOT) not in sys.path:
    sys.path.insert(0, str(ISOLATED_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_hybrid_group_test_2sym_floored as harness


def _load_base_runner() -> Any:
    module_name = "hybrid_group_test_2sym_base_for_floored_runtime"
    spec = importlib.util.spec_from_file_location(module_name, harness.BASE_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load base runner: {harness.BASE_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    harness.OUT_DIR.mkdir(parents=True, exist_ok=True)
    base_runner = _load_base_runner()
    base_runner.OUT_DIR = harness.OUT_DIR
    base_runner.train_grouped_interval_ga = (
        harness.floored_ga.train_grouped_interval_ga
    )
    base_runner.validate_grouped_gene = harness.floored_ga.validate_grouped_gene

    if base_runner.OUT_DIR != harness.OUT_DIR:
        raise AssertionError("floored output directory patch failed")
    if (
        base_runner.train_grouped_interval_ga
        is not harness.floored_ga.train_grouped_interval_ga
    ):
        raise AssertionError("floored GA patch failed")

    summary = base_runner.run()
    harness._apply_threshold_audit()
    harness._rename_trade_outputs()
    _, verdict = harness._build_three_way(summary)
    summary["generated_by"] = str(Path(__file__).relative_to(KINGMAKER_ROOT))
    summary["provisional_verdict_from_base_runner"] = summary.get(
        "provisional_verdict"
    )
    summary["provisional_verdict"] = verdict
    (harness.OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
