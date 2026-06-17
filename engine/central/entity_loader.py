"""Load Stage3 profile-catalog rows into central-controller entity records."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True)
class ConfidenceParams:
    method: str = "avg_expectancy"
    min_trade_count: int = 5
    low_trade_penalty: float = 0.5
    confidence_scale: float = 10.0


@dataclass(frozen=True)
class EntityRecord:
    entity_id: str
    ticker: str
    rulebook: dict
    rulebook_hash: str
    validation_metrics: dict
    validation_periods: list[dict]
    tags: dict
    confidence: float
    source_path: str = ""
    source_row_index: int = 0


def load_entities_from_stage3_dirs(
    stage3_dirs: Iterable[str | Path],
    *,
    params: Optional[ConfidenceParams] = None,
    require_eligible: bool = False,
) -> list[EntityRecord]:
    """Load entities from one or more standalone/batch Stage3 output dirs."""
    loaded: list[EntityRecord] = []
    for directory in stage3_dirs:
        path = Path(directory)
        catalog = path / "stage3_profile_catalog.jsonl"
        if not catalog.exists():
            raise FileNotFoundError(f"stage3_profile_catalog.jsonl not found: {path}")
        loaded.extend(load_entities_from_catalog(catalog, params=params, require_eligible=require_eligible))
    return loaded


def load_entities_from_catalog(
    catalog_path: str | Path,
    *,
    params: Optional[ConfidenceParams] = None,
    require_eligible: bool = False,
) -> list[EntityRecord]:
    path = Path(catalog_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows: list[EntityRecord] = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if require_eligible and payload.get("eligible_stage3_basic") is False:
            continue
        rows.append(entity_from_stage3_row(payload, params=params, source_path=str(path), source_row_index=idx))
    return rows


def entity_from_stage3_row(
    row: Mapping,
    *,
    params: Optional[ConfidenceParams] = None,
    source_path: str = "",
    source_row_index: int = 0,
) -> EntityRecord:
    rb = dict(row.get("rulebook") or {})
    ticker = str(row.get("ticker") or rb.get("ticker") or "").upper()
    rulebook_hash = str(row.get("rulebook_hash") or "")
    if not ticker:
        raise ValueError("stage3 row missing ticker")
    if not rb:
        raise ValueError(f"stage3 row missing rulebook for {ticker}")
    if not rulebook_hash:
        raise ValueError(f"stage3 row missing rulebook_hash for {ticker}")
    rb.setdefault("ticker", ticker)
    entity_id = f"{ticker}_{rulebook_hash[:12]}"
    period_results = row.get("period_results") or {}
    validation_metrics = pure_oos_metrics(period_results)
    validation_periods = list(row.get("pure_oos_validation_periods") or [])
    tags = {
        "holding_class": row.get("holding_class"),
        "risk_class": row.get("risk_class"),
        "return_class": row.get("return_class"),
        "composite_tag": row.get("composite_tag"),
        "rank": row.get("rank"),
        "entry_rank": row.get("entry_rank"),
        "exit_rank": row.get("exit_rank"),
    }
    confidence = compute_confidence(validation_metrics, params or ConfidenceParams())
    return EntityRecord(
        entity_id=entity_id,
        ticker=ticker,
        rulebook=rb,
        rulebook_hash=rulebook_hash,
        validation_metrics=validation_metrics,
        validation_periods=validation_periods,
        tags=tags,
        confidence=confidence,
        source_path=source_path,
        source_row_index=int(source_row_index or 0),
    )


def pure_oos_metrics(period_results) -> dict:
    """Return metrics for Stage3 periods whose role is pure_oos."""
    metrics: dict[str, dict] = {}
    if isinstance(period_results, Mapping):
        iterator = period_results.items()
    elif isinstance(period_results, list):
        iterator = ((str(row.get("label") or row.get("period") or i), row) for i, row in enumerate(period_results))
    else:
        iterator = []
    for label, row in iterator:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("role") or "").lower() != "pure_oos":
            continue
        m = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else row
        metrics[str(label)] = _metric_subset(m)
    return metrics


def compute_confidence(validation_metrics: Mapping[str, Mapping], params: ConfidenceParams) -> float:
    """Compute a simple scalar confidence from pure-OOS expectancy metrics."""
    rows = [dict(v or {}) for v in (validation_metrics or {}).values()]
    if not rows:
        return 0.0
    expectancies = [_float(r.get("expectancy_pct")) for r in rows]
    counts = [_float(r.get("trade_count")) for r in rows]
    if params.method == "min_expectancy":
        base = min(expectancies)
    elif params.method == "trade_weighted_expectancy":
        total = sum(max(c, 0.0) for c in counts)
        base = sum(e * max(c, 0.0) for e, c in zip(expectancies, counts)) / total if total > 0 else sum(expectancies) / len(expectancies)
    else:
        base = sum(expectancies) / len(expectancies)
    min_count = min(counts) if counts else 0.0
    penalty = 1.0
    if params.min_trade_count > 0 and min_count < params.min_trade_count:
        penalty = max(0.0, min(1.0, float(params.low_trade_penalty)))
    scale = abs(float(params.confidence_scale or 1.0)) or 1.0
    return float(base / scale * penalty)


def _metric_subset(metrics: Mapping) -> dict:
    keys = ("expectancy_pct", "win_rate", "profit_factor", "trade_count", "max_drawdown_pct")
    return {k: _float(metrics.get(k)) for k in keys}


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0
