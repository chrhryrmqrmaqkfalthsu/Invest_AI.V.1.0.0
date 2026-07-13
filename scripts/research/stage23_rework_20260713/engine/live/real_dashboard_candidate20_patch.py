"""Runtime patch to display 20 real-dashboard buy candidates.

The live candidate daemon keeps an 8-slot state for the compact slot board, but
real_dashboard_api already supports reading the sorted candidate_pool with
/api/real/candidate_slots?max_slots=N.  This patch only changes /dashboard-real
front-end requests and labels to request/display the top 20 real candidates.
It does not alter candidate scoring, live_slots_state generation, SAFETY guards,
order submission, reconciliation, or export logic.
"""
from __future__ import annotations

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_REAL_SLOT_OVERLAY_JS = None
_ORIG_REAL_DASHBOARD_HTML = None
DISPLAY_CANDIDATE_COUNT = 20


def _replace_candidate_count_text(text: str) -> str:
    text = text.replace(
        "fetch(`${API}/api/real/candidate_slots`, {cache:'no-store'})",
        f"fetch(`${{API}}/api/real/candidate_slots?max_slots={DISPLAY_CANDIDATE_COUNT}`, {{cache:'no-store'}})",
    )
    text = text.replace("매수 대기 후보 슬롯 (8)", f"매수 대기 후보 슬롯 ({DISPLAY_CANDIDATE_COUNT})")
    text = text.replace("상위 8개 후보", f"상위 {DISPLAY_CANDIDATE_COUNT}개 후보")
    text = text.replace("8개 후보", f"{DISPLAY_CANDIDATE_COUNT}개 후보")
    return text


def _patch_real_slot_overlay_candidate_count() -> None:
    global _ORIG_REAL_SLOT_OVERLAY_JS
    if _ORIG_REAL_SLOT_OVERLAY_JS is not None:
        return
    _ORIG_REAL_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    def patched_real_slot_overlay_js() -> str:
        return _replace_candidate_count_text(_ORIG_REAL_SLOT_OVERLAY_JS())

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js


def _patch_real_dashboard_html_candidate_count() -> None:
    global _ORIG_REAL_DASHBOARD_HTML
    if _ORIG_REAL_DASHBOARD_HTML is not None:
        return
    _ORIG_REAL_DASHBOARD_HTML = real_api._real_dashboard_html

    def patched_real_dashboard_html(base_module):
        response = _ORIG_REAL_DASHBOARD_HTML(base_module)
        try:
            body = response.body.decode("utf-8") if isinstance(response.body, (bytes, bytearray)) else str(response.body)
            response.body = _replace_candidate_count_text(body).encode("utf-8")
            response.headers["content-length"] = str(len(response.body))
        except Exception:
            pass
        return response

    real_api._real_dashboard_html = patched_real_dashboard_html


def install_real_dashboard_candidate20_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_real_slot_overlay_candidate_count()
    _patch_real_dashboard_html_candidate_count()
    _INSTALLED = True
