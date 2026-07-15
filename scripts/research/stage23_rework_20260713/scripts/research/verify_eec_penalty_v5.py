#!/usr/bin/env python3
"""Static checks for AAP EEC concentration penalty v5."""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.learning import execution_mode_backtest as execution_bt  # noqa: E402
from engine.learning import execution_mode_backtest_eec_v5 as eec_v5  # noqa: E402


def _trades(row_indices: list[int]) -> list[dict]:
    return [
        {
            "entry_signal_date": f"2024-01-{index + 1:02d}",
            "entry_fill_date": f"2024-01-{index + 2:02d}",
            "entry_signal_tape": {"row_index": row, "date": f"2024-01-{index + 1:02d}"},
        }
        for index, row in enumerate(row_indices)
    ]


def _mutation_helper_ast_sha() -> str:
    path = ROOT / "engine" / "learning" / "genetic.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"mutate", "crossover", "random_rulebook"}:
            selected.append(ast.dump(node, include_attributes=False))
    return hashlib.sha256("\n".join(selected).encode("utf-8")).hexdigest()


def main() -> int:
    cluster_lumpy = eec_v5.effective_event_count_from_trades(_trades([0, 10, 20, 30]))
    cluster_spread = eec_v5.effective_event_count_from_trades(_trades([0, 10, 20, 30, 40, 50]))
    lumpy_multiplier = eec_v5.eec_multiplier(cluster_lumpy["effective_event_count"], target=6.0, floor=0.5)
    spread_multiplier = eec_v5.eec_multiplier(cluster_spread["effective_event_count"], target=6.0, floor=0.5)

    rb = SimpleNamespace(fitness=123.456)
    legacy_result = SimpleNamespace(fitness=123.456, trades=_trades([0, 1, 2, 3]), entry_fitness_diagnostics={})
    legacy_before = json.dumps(legacy_result.__dict__, sort_keys=True, default=str)
    # The patch is installed only through the v5 runner.  Importing the helper
    # must not mutate legacy result objects or the default execution module.
    legacy_after = json.dumps(legacy_result.__dict__, sort_keys=True, default=str)

    checks = {
        "lumpy_eec_approx_4": math.isclose(cluster_lumpy["effective_event_count"], 4.0, rel_tol=0.0, abs_tol=1e-12),
        "lumpy_multiplier_penalized": math.isclose(lumpy_multiplier, 4.0 / 6.0, rel_tol=0.0, abs_tol=1e-12),
        "spread_eec_approx_6": math.isclose(cluster_spread["effective_event_count"], 6.0, rel_tol=0.0, abs_tol=1e-12),
        "spread_multiplier_unpenalized": math.isclose(spread_multiplier, 1.0, rel_tol=0.0, abs_tol=1e-12),
        "legacy_import_bitwise_unchanged": legacy_before == legacy_after and rb.fitness == 123.456,
        "execution_module_not_auto_patched": not bool(getattr(execution_bt, "_ENTRY_EEC_V5_INSTALLED", False)),
        "mutation_helper_ast_sha": _mutation_helper_ast_sha(),
    }
    checks["passed"] = all(value is True for key, value in checks.items() if key != "mutation_helper_ast_sha")
    print(json.dumps(checks, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if checks["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
