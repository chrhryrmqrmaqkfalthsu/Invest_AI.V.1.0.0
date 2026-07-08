"""Visibility/TIF fix for Alpaca reserved exit-order buttons.

The first Alpaca exit-order patch appended its UI JavaScript after the generated
real dashboard overlay IIFE.  Most helper functions used by the holding TP/SL
panel are local to that IIFE, so the appended code could not hook the panel and
the buttons were not visible.  This post-processor moves that injected code back
inside the same IIFE and also makes the frontend submit DAY TIF, which Alpaca
requires for fractional stock orders.
"""
from __future__ import annotations

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_SLOT_OVERLAY_JS = None

_OUTER_MARKER = "\n(function(){\n  function kmExitNum"
_IIFE_OPEN = "(function(){\n"
_IIFE_CLOSE = "\n})();"


def _patch_fractional_tif(js: str) -> str:
    # Fractional exit orders are rejected by Alpaca unless time_in_force=day.
    js = js.replace("time_in_force:'gtc'", "time_in_force:'day'")
    js = js.replace("time_in_force: 'gtc'", "time_in_force: 'day'")
    js = js.replace("기존 kingmaker 예약 주문은 취소 후 새로 겁니다.", "소수점 주식 규칙 때문에 DAY 주문으로 제출됩니다. 기존 kingmaker 예약 주문은 취소 후 새로 겁니다.")
    return js


def _move_alpaca_exit_iife_inside_overlay(js: str) -> str:
    if "kmApplyAlpacaExitOrder" not in js:
        return _patch_fractional_tif(js)
    start = js.find(_OUTER_MARKER)
    if start < 0:
        # Already inside the main overlay IIFE or injected in another shape.
        return _patch_fractional_tif(js)
    prefix = js[:start]
    outer = js[start + 1 :].strip()
    if not outer.startswith(_IIFE_OPEN) or not outer.endswith(_IIFE_CLOSE.strip()):
        return _patch_fractional_tif(js)
    body = outer[len(_IIFE_OPEN) :]
    if body.endswith(_IIFE_CLOSE.strip()):
        body = body[: -len(_IIFE_CLOSE.strip())]
    close = prefix.rfind("})();")
    if close < 0:
        return _patch_fractional_tif(js)
    if "window.__kmAlpacaExitButtonsInsideOverlay" in prefix:
        return _patch_fractional_tif(prefix)
    wrapped_body = "\n  window.__kmAlpacaExitButtonsInsideOverlay=true;\n" + body.rstrip() + "\n"
    return _patch_fractional_tif(prefix[:close] + wrapped_body + prefix[close:])


def install_real_dashboard_alpaca_exit_order_visibility_patch() -> None:
    """Install the JS placement/TIF fix once per API process."""
    global _INSTALLED, _ORIG_SLOT_OVERLAY_JS
    if _INSTALLED:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    def patched_real_slot_overlay_js() -> str:
        return _move_alpaca_exit_iife_inside_overlay(_ORIG_SLOT_OVERLAY_JS())

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js
    _INSTALLED = True
