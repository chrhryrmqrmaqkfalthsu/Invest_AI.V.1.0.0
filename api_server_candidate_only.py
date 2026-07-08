"""KINGMAKER dashboard API wrapper with live candidate-only display override.

This module imports the normal aftermarket dashboard app, then replaces
/api/live/central_candidates so candidate-only BUY mode is shown first and capped
to 8 rows.  It does not alter /api/real/* isolation.
"""
from __future__ import annotations

import api_server_aftermarket as _aftermarket
from engine.live.live_candidate_display_routes import install_live_candidate_display_routes
from engine.live.real_dashboard_holding_days_patch import install_real_dashboard_holding_days_patch

app = _aftermarket.app
install_live_candidate_display_routes(app, _aftermarket._base, max_candidates=8)
install_real_dashboard_holding_days_patch()
