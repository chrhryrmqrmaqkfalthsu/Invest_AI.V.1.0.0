"""Windows-only fallback for Loguru's optional win32-setctime dependency.

The learning workers do not depend on Windows file creation-time mutation.
Loguru checks ``SUPPORTED`` before attempting to call ``setctime``.
"""
from __future__ import annotations

from typing import Any

SUPPORTED = False


def setctime(filepath: str, timestamp: float, *args: Any, **kwargs: Any) -> None:
    """No-op implementation used only when win32-setctime is unavailable."""
    return None
