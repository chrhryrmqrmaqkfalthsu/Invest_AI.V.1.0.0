#!/usr/bin/env python3
"""Safe research entrypoint for the two-symbol hybrid runner.

The copied indicator module imports the copied production logger, which in turn
requires a copied policy.yaml and creates copied log files.  The research pilot
needs none of that state.  This entrypoint injects a no-op logger module before
loading the runner so feature formulas remain identical while no config, .env,
or logging side effect is introduced.
"""
from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path


class _NoopLogger:
    def debug(self, *args, **kwargs) -> None:
        return None

    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None

    def success(self, *args, **kwargs) -> None:
        return None

    def bind(self, *args, **kwargs) -> "_NoopLogger":
        return self


logger_stub = types.ModuleType("engine.core.logger")
logger_stub.get_logger = lambda name="": _NoopLogger()
logger_stub.trade_logger = lambda: _NoopLogger()
sys.modules["engine.core.logger"] = logger_stub

runner = Path(__file__).with_name("run_hybrid_group_test_2sym.py")
runpy.run_path(str(runner), run_name="__main__")
