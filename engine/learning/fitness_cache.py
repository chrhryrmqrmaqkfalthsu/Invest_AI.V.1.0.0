"""In-memory fitness cache for GA evaluate_fn wrappers.

The cache is intentionally process-local and non-persistent.  It does not alter
GA generation/mutation/crossover/selection logic; callers wrap the injected
``evaluate_fn`` before passing it to ``run_ga``.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from engine.core.metadata import compute_rulebook_hash

CACHE_SCHEMA_VERSION = 1

_REQUIRED_KEY_CTX_FIELDS: tuple[str, ...] = (
    "ticker",
    "period_label",
    "start_date",
    "end_date",
    "entry_execution_mode",
    "exit_execution_mode",
    "fold_exit_policy",
    "fitness_mode",
    "code_commit",
    "add_buy_runtime_enabled",
    "cache_schema_version",
)


@dataclass
class FitnessCache:
    """Simple process-local GA fitness cache with hit/miss counters."""

    store: dict[Any, Any] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    @property
    def unique_keys(self) -> int:
        return len(self.store)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return float(self.hits / total) if total else 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "hits": int(self.hits),
            "misses": int(self.misses),
            "hit_rate": self.hit_rate,
            "unique_keys": self.unique_keys,
        }


def fitness_cache_disabled_by_env() -> bool:
    """Return True when the process env explicitly disables the fitness cache."""

    raw = os.environ.get("KINGMAKER_FITNESS_CACHE")
    if raw is None:
        raw = os.environ.get("FITNESS_CACHE")
    if raw is None:
        return False
    return str(raw).strip().lower() in {"0", "false", "off", "no", "disabled"}


def resolve_code_commit(project_root: str | Path | None = None) -> str:
    """Resolve the current git commit once for cache-key isolation.

    Environments without git can inject ``KINGMAKER_CODE_COMMIT`` or
    ``CODE_COMMIT``.  If neither git nor env is available, ``"unknown"`` is used
    rather than failing a research run.
    """

    for env_key in ("KINGMAKER_CODE_COMMIT", "CODE_COMMIT", "GIT_COMMIT"):
        value = os.environ.get(env_key)
        if value:
            return str(value).strip()

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_root) if project_root is not None else None,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        commit = completed.stdout.strip()
        return commit or "unknown"
    except Exception:
        return "unknown"


def _require_key_ctx(key_ctx: Mapping[str, Any], field_name: str) -> Any:
    if field_name not in key_ctx:
        raise KeyError(f"missing fitness cache key context: {field_name}")
    return key_ctx[field_name]


def _normalize_key_part(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def build_cache_key(rulebook: Any, key_ctx: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Build a collision-resistant key for a deterministic backtest fitness call."""

    for field_name in _REQUIRED_KEY_CTX_FIELDS:
        _require_key_ctx(key_ctx, field_name)

    schema_version = int(key_ctx.get("cache_schema_version", CACHE_SCHEMA_VERSION))
    return (
        ("cache_schema_version", schema_version),
        ("rulebook_hash", compute_rulebook_hash(rulebook)),
        ("ticker", _normalize_key_part(key_ctx["ticker"])),
        ("period_label", _normalize_key_part(key_ctx["period_label"])),
        ("start_date", _normalize_key_part(key_ctx["start_date"])),
        ("end_date", _normalize_key_part(key_ctx["end_date"])),
        ("entry_execution_mode", _normalize_key_part(key_ctx["entry_execution_mode"])),
        ("exit_execution_mode", _normalize_key_part(key_ctx["exit_execution_mode"])),
        ("fold_exit_policy", _normalize_key_part(key_ctx["fold_exit_policy"])),
        ("fitness_mode", _normalize_key_part(key_ctx["fitness_mode"])),
        ("code_commit", _normalize_key_part(key_ctx["code_commit"])),
        ("add_buy_runtime_enabled", bool(key_ctx["add_buy_runtime_enabled"])),
    )


def make_cached_evaluate_fn(
    raw_evaluate_fn: Callable[[Any], Any],
    *,
    cache: FitnessCache,
    key_ctx: Mapping[str, Any],
) -> Callable[[Any], Any]:
    """Wrap ``raw_evaluate_fn(rulebook)->fitness`` with an in-memory cache."""

    def cached(rulebook: Any) -> Any:
        key = build_cache_key(rulebook, key_ctx)
        if key in cache.store:
            cache.hits += 1
            return cache.store[key]
        cache.misses += 1
        value = raw_evaluate_fn(rulebook)
        cache.store[key] = value
        return value

    return cached


def disabled_cache_summary() -> dict[str, Any]:
    return {
        "enabled": False,
        "hits": 0,
        "misses": 0,
        "hit_rate": 0.0,
        "unique_keys": 0,
    }


def summarize_fitness_cache(cache: FitnessCache | None) -> dict[str, Any]:
    return cache.summary() if cache is not None else disabled_cache_summary()


def aggregate_fitness_cache_summaries(summaries: list[Mapping[str, Any]]) -> dict[str, Any]:
    enabled = any(bool(summary.get("enabled", True)) for summary in summaries)
    hits = sum(int(summary.get("hits", 0) or 0) for summary in summaries)
    misses = sum(int(summary.get("misses", 0) or 0) for summary in summaries)
    unique_keys = sum(int(summary.get("unique_keys", 0) or 0) for summary in summaries)
    total = hits + misses
    return {
        "enabled": bool(enabled),
        "hits": hits,
        "misses": misses,
        "hit_rate": float(hits / total) if total else 0.0,
        "unique_keys": unique_keys,
    }


def make_cache_key_context(
    *,
    ticker: str,
    period_label: str,
    start_date: Any,
    end_date: Any,
    entry_execution_mode: str,
    exit_execution_mode: str,
    fold_exit_policy: str,
    fitness_mode: str,
    code_commit: str,
    add_buy_runtime_enabled: bool = False,
    cache_schema_version: int = CACHE_SCHEMA_VERSION,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "entry_execution_mode": entry_execution_mode,
        "exit_execution_mode": exit_execution_mode,
        "fold_exit_policy": fold_exit_policy,
        "fitness_mode": fitness_mode,
        "code_commit": code_commit,
        "add_buy_runtime_enabled": bool(add_buy_runtime_enabled),
        "cache_schema_version": int(cache_schema_version),
    }
