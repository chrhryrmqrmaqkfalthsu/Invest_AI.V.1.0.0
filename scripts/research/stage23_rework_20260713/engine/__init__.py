"""Kingmaker research engine package bootstrap."""
from __future__ import annotations

import os
import sys


if os.name == "nt" and "win32_setctime" not in sys.modules:
    try:
        __import__("win32_setctime")
    except ModuleNotFoundError:
        from engine import _win32_setctime_compat as _win32_setctime_compat

        sys.modules["win32_setctime"] = _win32_setctime_compat
