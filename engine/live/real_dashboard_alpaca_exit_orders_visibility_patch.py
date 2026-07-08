"""Visibility fix for Alpaca reserved exit-order buttons.

The first Alpaca exit-order patch appended its UI JavaScript after the generated
real dashboard overlay IIFE.  Most helper functions used by the holding TP/SL
panel are local to that IIFE, so the appended code could not hook the panel and
the buttons were not visible.  This post-processor moves that injected code back
inside the same IIFE without changing the order-submission routes.
"""
from __future__ import annotations

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_SLOT_OVERLAY_JS = None

_OUTER_MARKER = "\n(function(){\n  function kmExitNum"
_IIFE_OPEN = "(function(){\n"
_IIFE_CLOSE = "\n})();"


def _move_alpaca_exit_iife_inside_overlay(js: str) -> str:
    if "kmApplyAlpacaExitOrder" not in js:
        return js
    start = js.find(_OUTER_MARKER)
    if start < 0:
        # Already inside the main overlay IIFE or injected in another shape.
        return js
    prefix = js[:start]
    outer = js[start + 1 :].strip()
    if not outer.startswith(_IIFE_OPEN) or not outer.endswith(_IIFE_CLOSE.strip()):
        return js
    body = outer[len(_IIFE_OPEN) :]
    if body.endswith(_IIFE_CLOSE.strip()):
        body = body[: -len(_IIFE_CLOSE.strip())]
    close = prefix.rfind("})();")
    if close < 0:
        return js
    if "window.__kmAlpacaExitButtonsInsideOverlay" in prefix:
        return prefix
    wrapped_body = "\n  window.__kmAlpacaExitButtonsInsideOverlay=true;\n" + body.rstrip() + "\n"
    return prefix[:close] + wrapped_body + prefix[close:]


def install_real_dashboard_alpaca_exit_order_visibility_patch() -> None:
    """Install the JS placement fix once per API process."""
    global _INSTALLED, _ORIG_SLOT_OVERLAY_JS
    if _INSTALLED:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    def patched_real_slot_overlay_js() -> str:
        return _move_alpaca_exit_iife_inside_overlay(_ORIG_SLOT_OVERLAY_JS())

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js
    _INSTALLED = True
