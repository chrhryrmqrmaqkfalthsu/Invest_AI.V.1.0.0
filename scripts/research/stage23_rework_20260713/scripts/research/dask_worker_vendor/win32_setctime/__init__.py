"""Minimal Windows compatibility shim used by bundled Loguru.

Loguru treats ``SUPPORTED = False`` as a signal to skip creation-time updates.
The research fitness path does not depend on Windows file creation timestamps.
"""
from __future__ import annotations

from typing import Any

SUPPORTED = False


def setctime(filepath: str, timestamp: float, *args: Any, **kwargs: Any) -> None:
    """No-op fallback; Loguru checks ``SUPPORTED`` before calling this."""
    return None
