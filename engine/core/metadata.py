"""Standard metadata and hash utilities for Kingmaker artifacts.

This module is intentionally self-contained. It does not import engine runtime
modules, so it can be used safely by diagnostic, true-WF, full learn, ensemble,
and live paths without introducing circular dependencies.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, Mapping, Optional


EXCLUDED_RULEBOOK_HASH_FIELDS = {
    "fitness",
    "win_rate",
    "avg_return_pct",
    "expectancy_pct",
    "max_drawdown_pct",
    "trade_count",
    "generated_at",
}

DEFAULT_FEATURE_LAG = {
    "ticker_sentiment_days": 1,
    "market_events_days": 1,
}


def _public_object_dict(obj: Any) -> Dict[str, Any]:
    """Best-effort conversion of an arbitrary object into a public dict."""
    if obj is None:
        return {}

    if isinstance(obj, Mapping):
        return dict(obj)

    if dataclasses.is_dataclass(obj):
        try:
            return dataclasses.asdict(obj)
        except Exception:
            return {}

    for method_name in ("model_dump", "dict", "to_dict"):
        method = getattr(obj, method_name, None)
        if callable(method):
            try:
                value = method()
                if isinstance(value, Mapping):
                    return dict(value)
            except Exception:
                pass

    raw = getattr(obj, "__dict__", None)
    if isinstance(raw, Mapping):
        return {k: v for k, v in raw.items() if not str(k).startswith("_")}

    result: Dict[str, Any] = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if callable(value):
            continue
        result[name] = value
    return result


def _json_safe(value: Any) -> Any:
    """Convert values into deterministic JSON-serializable structures."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Mapping):
        return {
            str(k): _json_safe(v)
            for k, v in value.items()
            if not str(k).startswith("_")
        }

    if dataclasses.is_dataclass(value):
        try:
            return _json_safe(dataclasses.asdict(value))
        except Exception:
            return str(value)

    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]

    if hasattr(value, "model_dump") or hasattr(value, "dict") or hasattr(value, "to_dict") or hasattr(value, "__dict__"):
        return _json_safe(_public_object_dict(value))

    return str(value)


def _strip_excluded_fields(value: Any) -> Any:
    """Recursively remove performance/runtime fields from a JSON-safe value."""
    safe_value = _json_safe(value)

    if isinstance(safe_value, Mapping):
        return {
            str(k): _strip_excluded_fields(v)
            for k, v in safe_value.items()
            if str(k) not in EXCLUDED_RULEBOOK_HASH_FIELDS and not str(k).startswith("_")
        }

    if isinstance(safe_value, list):
        return [_strip_excluded_fields(v) for v in safe_value]

    return safe_value


def canonical_rulebook_dict(rb_or_dict: Any) -> Dict[str, Any]:
    """Return a canonical rulebook dict containing strategy parameters only.

    Performance and runtime-varying fields are excluded so the same strategy
    receives the same hash even when evaluated on different periods.
    """
    if rb_or_dict is None:
        return {}

    base = _public_object_dict(rb_or_dict)
    stripped = _strip_excluded_fields(base)
    return stripped if isinstance(stripped, dict) else {}


def compute_rulebook_hash(rb_or_dict: Any) -> str:
    """Compute a deterministic SHA-256 hash for a rulebook."""
    canonical = canonical_rulebook_dict(rb_or_dict)
    payload = json.dumps(
        canonical,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_member_hash(rb_or_dict: Any) -> str:
    """Compute an individual member hash.

    The first implementation intentionally reuses the rulebook hash because a
    member is currently identified by its strategy parameter set.
    """
    return compute_rulebook_hash(rb_or_dict)


def ga_config_to_dict(ga_cfg: Any, ga_result: Optional[Any] = None) -> Dict[str, Any]:
    """Convert GA config/result objects into a safe metadata dict."""
    cfg = _public_object_dict(ga_cfg)
    result = _public_object_dict(ga_result)

    ga: Dict[str, Any] = {}
    for key in ("population", "population_size", "generations", "seed"):
        if key in cfg:
            normalized_key = "population" if key == "population_size" else key
            ga[normalized_key] = _json_safe(cfg[key])

    if "generations_run" in result:
        ga["generations_run"] = _json_safe(result["generations_run"])
    elif "generation" in result:
        ga["generations_run"] = _json_safe(result["generation"])

    for key in ("best_fitness", "fitness_mode"):
        if key in result:
            ga[key] = _json_safe(result[key])
        elif key in cfg:
            ga[key] = _json_safe(cfg[key])

    return ga


def build_metadata(
    *,
    source: Optional[str] = None,
    ticker: Optional[str] = None,
    fitness_mode: Optional[str] = None,
    data_start: Optional[Any] = None,
    data_end: Optional[Any] = None,
    train_period: Optional[Any] = None,
    test_period: Optional[Any] = None,
    oos_periods: Optional[Any] = None,
    ga_cfg: Optional[Any] = None,
    ga_result: Optional[Any] = None,
    ga: Optional[Any] = None,
    rulebook: Optional[Any] = None,
    member: Optional[Any] = None,
    rulebook_hash: Optional[str] = None,
    member_hash: Optional[str] = None,
    validation: Optional[Any] = None,
    feature_lag: Optional[Any] = None,
    run_id: Optional[str] = None,
    created_at: Optional[Any] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """Build the standard ``_meta`` dict for Kingmaker artifacts.

    Missing values are represented by safe empty defaults so callers can attach
    metadata incrementally without breaking old paths.
    """
    created = created_at or datetime.now(timezone.utc)
    if isinstance(created, datetime):
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        created_str = created.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        created_str = str(_json_safe(created))

    merged_ga = ga_config_to_dict(ga_cfg, ga_result)
    if ga is not None:
        ga_dict = _public_object_dict(ga)
        if not ga_dict and isinstance(_json_safe(ga), dict):
            ga_dict = _json_safe(ga)
        merged_ga.update(_json_safe(ga_dict))

    meta: Dict[str, Any] = {
        "run_id": run_id or str(uuid.uuid4()),
        "created_at": created_str,
        "source": source or "",
        "ticker": ticker or "",
        "fitness_mode": fitness_mode or "",
        "data_start": _json_safe(data_start) if data_start is not None else "",
        "data_end": _json_safe(data_end) if data_end is not None else "",
        "train_period": _json_safe(train_period) if train_period is not None else [],
        "test_period": _json_safe(test_period) if test_period is not None else [],
        "oos_periods": _json_safe(oos_periods) if oos_periods is not None else [],
        "ga": merged_ga,
        "rulebook_hash": rulebook_hash or compute_rulebook_hash(rulebook),
        "member_hash": member_hash or compute_member_hash(member if member is not None else rulebook),
        "validation": _json_safe(validation) if validation is not None else {},
        "feature_lag": _json_safe(feature_lag) if feature_lag is not None else dict(DEFAULT_FEATURE_LAG),
    }

    for key, value in extra.items():
        if key.startswith("_"):
            continue
        meta[key] = _json_safe(value)

    return meta
