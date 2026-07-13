"""Live-only direct Event policy and shadow diagnostics.

This module controls only direct ``event_flags`` passed to live
``evaluate_signal`` calls. It deliberately leaves ``MarketContext.score``
and its aggregate macro adjustment unchanged.
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("live_event_policy")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SHADOW_LOG_DIR = PROJECT_ROOT / "data" / "_system" / "analysis" / "shadow_direct_event"

EVENT_FLAG_MAP: tuple[tuple[str, str], ...] = (
    ("has_war", "전쟁"),
    ("has_rate_hike", "금리정책_인상"),
    ("has_rate_cut", "금리정책_인하"),
    ("has_geopolitical", "지정학_긴장"),
    ("has_tariff", "관세"),
    ("has_export_ban", "수출규제"),
    ("has_earnings_shock", "실적쇼크"),
    ("has_oil_surge", "유가급등"),
    ("has_banking_crisis", "은행위기"),
    ("has_inflation", "인플레이션"),
    ("has_fed_statement", "연준발언"),
)


def _coerce_policy_bool(value: Any, *, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return default
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def live_direct_event_enabled() -> bool:
    """Read the live switch, falling back to the current ON behavior."""
    try:
        # Lazy import is intentional. A config import/load failure must preserve
        # the existing live behavior instead of disabling Event unexpectedly.
        from engine.core.config import config

        raw = config.get("live.direct_event_enabled", True)
    except Exception as exc:
        log.warning("live direct Event policy load failed; preserving ON default: %s", exc)
        return True
    return _coerce_policy_bool(raw, default=True)


def live_event_flags(ctx: Any, enabled_override: bool | None = None) -> dict[str, int] | None:
    """Return the legacy 11 flags when enabled, otherwise ``None``.

    ``enabled_override`` is for shadow comparison. The ON mapping intentionally
    preserves the previous key order and membership semantics exactly.
    """
    enabled = live_direct_event_enabled() if enabled_override is None else bool(enabled_override)
    if not enabled:
        return None
    try:
        active = getattr(ctx, "active_events", {}) or {} if ctx is not None else {}
        return {flag: int(event_name in active) for flag, event_name in EVENT_FLAG_MAP}
    except Exception:
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def append_shadow_direct_event_log(
    *,
    candidate_id: str,
    mode: str,
    path: str,
    market_score_on: float,
    market_score_off: float,
    result_on: Any,
    result_off: Any,
) -> Path | None:
    """Append one ON/OFF row without affecting the actual live result."""
    try:
        timestamp = datetime.now(timezone.utc)
        components_on = dict(getattr(result_on, "components", {}) or {})
        event_component = _safe_float(components_on.get("events", 0.0), 0.0)
        score_on = _safe_float(getattr(result_on, "score", 0.0), 0.0)
        score_off = _safe_float(getattr(result_off, "score", 0.0), 0.0)
        raw_score_on = _safe_float(getattr(result_on, "raw_score", 0.0), 0.0)
        raw_score_off = _safe_float(getattr(result_off, "raw_score", 0.0), 0.0)
        threshold = _safe_float(getattr(result_on, "threshold", 0.0), 0.0)
        market_adjustment_on = _safe_float(getattr(result_on, "market_adjustment", 1.0), 1.0)
        market_adjustment_off = _safe_float(getattr(result_off, "market_adjustment", 1.0), 1.0)
        market_score_on_f = _safe_float(market_score_on, 50.0)
        market_score_off_f = _safe_float(market_score_off, 50.0)
        score_delta = score_on - score_off
        expected_score_delta = event_component * market_adjustment_on
        raw_score_delta = raw_score_on - raw_score_off
        invariant_market_score = market_score_on_f == market_score_off_f
        invariant_market_adjustment = market_adjustment_on == market_adjustment_off
        invariant_raw_delta = math.isclose(raw_score_delta, event_component, rel_tol=0.0, abs_tol=1e-12)
        invariant_score_delta = math.isclose(score_delta, expected_score_delta, rel_tol=0.0, abs_tol=1e-12)
        payload = {
            "timestamp": timestamp.isoformat(),
            "candidate_id": str(candidate_id or ""),
            "mode": str(mode or "unknown"),
            "path": str(path or "unknown"),
            "market_score_on": market_score_on_f,
            "market_score_off": market_score_off_f,
            "event_component": event_component,
            "score_on": score_on,
            "score_off": score_off,
            "raw_score_on": raw_score_on,
            "raw_score_off": raw_score_off,
            "pass_on": bool(getattr(result_on, "should_buy", False)),
            "pass_off": bool(getattr(result_off, "should_buy", False)),
            "threshold": threshold,
            "market_adjustment_on": market_adjustment_on,
            "market_adjustment_off": market_adjustment_off,
            "score_delta": score_delta,
            "expected_score_delta": expected_score_delta,
            "invariant_market_score": invariant_market_score,
            "invariant_market_adjustment": invariant_market_adjustment,
            "invariant_raw_delta": invariant_raw_delta,
            "invariant_score_delta": invariant_score_delta,
            "invariant_ok": bool(
                invariant_market_score
                and invariant_market_adjustment
                and invariant_raw_delta
                and invariant_score_delta
            ),
        }
        SHADOW_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = SHADOW_LOG_DIR / f"shadow_direct_event_{timestamp:%Y%m%d}.jsonl"
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with log_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return log_path
    except Exception as exc:
        log.warning("direct Event shadow log skipped without affecting live result: %s", exc)
        return None
