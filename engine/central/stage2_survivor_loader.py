"""Load Stage2 survivor rows into central-controller EntityRecord objects.

Stage2 survivors are not Stage3 profile-catalog rows. The central index carries
metrics and points to the per-ticker survivors.jsonl that contains the actual
rulebook. This adapter resolves that boundary without modifying batch outputs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from engine.central.entity_loader import EntityRecord
from engine.core.metadata import compute_member_hash, compute_rulebook_hash

DEFAULT_SELL_OMEN_SCORE_PATH = Path("data/_system/ml_sell_omen/sell_omen_scores_lr8d85.csv")
METRIC_KEYS = ("expectancy_pct", "win_rate", "profit_factor", "trade_count", "max_drawdown_pct", "fitness")
PERIOD_WINDOWS = {
    "stress_pre_2022h1": {"start": "1900-01-01", "end": "2022-06-30", "role": "stage2_stress"},
    "train_1_eval": {"start": "2022-07-01", "end": "2023-06-30", "role": "stage2_eval"},
    "train_2_eval": {"start": "2023-07-01", "end": "2024-06-30", "role": "stage2_eval"},
    "train_3_eval": {"start": "2024-07-01", "end": "2025-06-30", "role": "stage2_eval"},
    "oos_2025h2": {"start": "2025-07-01", "end": "2099-12-31", "role": "stage2_oos"},
}
PERIOD_ORDER = ("stress_pre_2022h1", "train_1_eval", "train_2_eval", "train_3_eval", "oos_2025h2")


class Stage2SurvivorLoaderError(ValueError):
    """Raised when Stage2 survivor artifacts cannot be safely loaded."""


@dataclass(frozen=True)
class Stage2LoadReport:
    entities: list[EntityRecord] = field(default_factory=list)
    central_index_rows: int = 0
    stage2_survivor_rows: int = 0
    loaded: int = 0
    skipped_hash_mismatch: int = 0
    missing_source_files: int = 0
    unmatched_rulebook_hashes: int = 0
    skipped_ticker_filter: int = 0


@dataclass(frozen=True)
class SellOmenCoverageReport:
    score_table_path: str
    score_table_exists: bool
    entity_count: int
    unique_tickers: int
    covered: int
    missing: int
    covered_tickers: tuple[str, ...]
    missing_tickers: tuple[str, ...]


def load_stage2_survivors(
    central_index_path: str | Path,
    batch_root: str | Path,
    *,
    tickers: Optional[Iterable[str]] = None,
    require_hash_match: bool = True,
) -> list[EntityRecord]:
    """Load Stage2 survivors as EntityRecord objects.

    The returned EntityRecord has the same interface as Stage3 entities so it can
    flow into policy_search/backtester unchanged. Confidence is intentionally 0;
    policy_search should recompute it from validation_metrics.
    """
    return load_stage2_survivors_with_report(
        central_index_path,
        batch_root,
        tickers=tickers,
        require_hash_match=require_hash_match,
    ).entities


def load_stage2_survivors_with_report(
    central_index_path: str | Path,
    batch_root: str | Path,
    *,
    tickers: Optional[Iterable[str]] = None,
    require_hash_match: bool = True,
) -> Stage2LoadReport:
    path = Path(central_index_path)
    root = Path(batch_root)
    ticker_filter = {_normalize_ticker(t) for t in tickers or []}
    index_rows = _load_jsonl(path)
    entities: list[EntityRecord] = []
    stage2_rows = 0
    skipped_hash_mismatch = 0
    missing_source_files = 0
    unmatched_rulebook_hashes = 0
    skipped_ticker_filter = 0

    for row_idx, index_row in enumerate(index_rows, start=1):
        if index_row.get("event_type") != "stage2_survivor":
            continue
        stage2_rows += 1
        ticker = _normalize_ticker(index_row.get("ticker"))
        if ticker_filter and ticker not in ticker_filter:
            skipped_ticker_filter += 1
            continue
        source_path = _survivor_source_path(root, index_row)
        if not source_path.exists():
            missing_source_files += 1
            if require_hash_match:
                raise FileNotFoundError(f"survivors.jsonl missing for {ticker}: {source_path}")
            continue
        survivor, survivor_line = _find_survivor_by_hash(source_path, str(index_row.get("rulebook_hash") or ""))
        if survivor is None:
            unmatched_rulebook_hashes += 1
            if require_hash_match:
                raise Stage2SurvivorLoaderError(f"rulebook_hash not found in {source_path}: {index_row.get('rulebook_hash')}")
            continue
        try:
            entity = entity_from_stage2_survivor_row(index_row, survivor, source_path=str(source_path), source_row_index=survivor_line)
        except Stage2SurvivorLoaderError:
            if require_hash_match:
                raise
            skipped_hash_mismatch += 1
            continue
        entities.append(entity)

    return Stage2LoadReport(
        entities=entities,
        central_index_rows=len(index_rows),
        stage2_survivor_rows=stage2_rows,
        loaded=len(entities),
        skipped_hash_mismatch=skipped_hash_mismatch,
        missing_source_files=missing_source_files,
        unmatched_rulebook_hashes=unmatched_rulebook_hashes,
        skipped_ticker_filter=skipped_ticker_filter,
    )


def entity_from_stage2_survivor_row(
    central_index_row: Mapping[str, Any],
    survivor_row: Mapping[str, Any],
    *,
    source_path: str = "",
    source_row_index: int = 0,
) -> EntityRecord:
    ticker = _normalize_ticker(central_index_row.get("ticker") or survivor_row.get("ticker"))
    rulebook = dict(survivor_row.get("rulebook") or {})
    rulebook_hash = str(central_index_row.get("rulebook_hash") or survivor_row.get("rulebook_hash") or "").strip()
    if not ticker:
        raise Stage2SurvivorLoaderError("stage2 survivor missing ticker")
    if not rulebook:
        raise Stage2SurvivorLoaderError(f"stage2 survivor missing rulebook for {ticker}")
    if not rulebook_hash:
        raise Stage2SurvivorLoaderError(f"stage2 survivor missing rulebook_hash for {ticker}")
    rb_ticker = _normalize_ticker(rulebook.get("ticker"))
    survivor_ticker = _normalize_ticker(survivor_row.get("ticker"))
    if rb_ticker and rb_ticker != ticker:
        raise Stage2SurvivorLoaderError(f"ticker mismatch: central={ticker} rulebook={rb_ticker}")
    if survivor_ticker and survivor_ticker != ticker:
        raise Stage2SurvivorLoaderError(f"ticker mismatch: central={ticker} survivor={survivor_ticker}")
    computed_rulebook_hash = compute_rulebook_hash(rulebook)
    computed_member_hash = compute_member_hash(rulebook)
    if computed_rulebook_hash != rulebook_hash:
        raise Stage2SurvivorLoaderError(f"rulebook_hash mismatch for {ticker}: row={rulebook_hash} computed={computed_rulebook_hash}")
    if computed_member_hash != rulebook_hash:
        raise Stage2SurvivorLoaderError(f"member_hash mismatch for {ticker}: row={rulebook_hash} computed={computed_member_hash}")
    if _normalize_ticker(rulebook.get("direction")) != "LONG":
        raise Stage2SurvivorLoaderError(f"stage2 survivor direction must be long for {ticker}: {rulebook.get('direction')!r}")
    rulebook["ticker"] = ticker
    metrics = _validation_metrics_from_stage2(central_index_row.get("metrics") or {}, survivor_row.get("periods") or [])
    periods = _validation_periods(metrics)
    tags = {
        "stage": "stage2",
        "origin_train_labels": list(survivor_row.get("origin_train_labels") or central_index_row.get("origin_train_labels") or []),
        "origin_count": int(survivor_row.get("origin_count") or central_index_row.get("origin_count") or 0),
        "source_file": central_index_row.get("source_file"),
        "source_row_index": central_index_row.get("source_row_index"),
        "attempt_dir": central_index_row.get("attempt_dir"),
        "run_id": central_index_row.get("run_id"),
    }
    return EntityRecord(
        entity_id=f"{ticker}_{rulebook_hash[:12]}",
        ticker=ticker,
        rulebook=rulebook,
        rulebook_hash=rulebook_hash,
        validation_metrics=metrics,
        validation_periods=periods,
        tags=tags,
        confidence=0.0,
        source_path=str(source_path or ""),
        source_row_index=int(source_row_index or central_index_row.get("source_row_index") or 0),
    )


def sell_omen_coverage_report(
    entities: Iterable[EntityRecord],
    score_table_path: str | Path = DEFAULT_SELL_OMEN_SCORE_PATH,
) -> SellOmenCoverageReport:
    entity_list = list(entities)
    tickers = sorted({_normalize_ticker(entity.ticker) for entity in entity_list if _normalize_ticker(entity.ticker)})
    path = Path(score_table_path)
    if not path.exists():
        return SellOmenCoverageReport(
            score_table_path=str(path),
            score_table_exists=False,
            entity_count=len(entity_list),
            unique_tickers=len(tickers),
            covered=0,
            missing=len(tickers),
            covered_tickers=(),
            missing_tickers=tuple(tickers),
        )
    table = pd.read_csv(path, usecols=["ticker"])
    scored = set(table["ticker"].astype(str).str.upper())
    covered = tuple(t for t in tickers if t in scored)
    missing = tuple(t for t in tickers if t not in scored)
    return SellOmenCoverageReport(
        score_table_path=str(path),
        score_table_exists=True,
        entity_count=len(entity_list),
        unique_tickers=len(tickers),
        covered=len(covered),
        missing=len(missing),
        covered_tickers=covered,
        missing_tickers=missing,
    )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception as exc:
            raise Stage2SurvivorLoaderError(f"invalid JSONL at {path}:{lineno}: {exc}") from exc
        if not isinstance(payload, dict):
            raise Stage2SurvivorLoaderError(f"JSONL row must be object at {path}:{lineno}")
        rows.append(payload)
    return rows


def _survivor_source_path(batch_root: Path, index_row: Mapping[str, Any]) -> Path:
    artifact_paths = index_row.get("artifact_paths") if isinstance(index_row.get("artifact_paths"), Mapping) else {}
    rel = artifact_paths.get("survivors") or index_row.get("source_file")
    if not rel:
        raise Stage2SurvivorLoaderError(f"stage2 survivor source path missing for {index_row.get('ticker')}")
    path = Path(str(rel))
    return path if path.is_absolute() else batch_root / path


def _find_survivor_by_hash(path: Path, rulebook_hash: str) -> tuple[Optional[dict[str, Any]], int]:
    wanted = str(rulebook_hash or "").strip()
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if str(payload.get("rulebook_hash") or "").strip() == wanted:
            return payload, idx
    return None, 0


def _validation_metrics_from_stage2(metrics: Mapping[str, Any], periods: Iterable[Mapping[str, Any]]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if isinstance(metrics, Mapping):
        for label in PERIOD_ORDER:
            if isinstance(metrics.get(label), Mapping):
                out[label] = _metric_subset(metrics[label])
        for label, value in metrics.items():
            if label not in out and isinstance(value, Mapping):
                out[str(label)] = _metric_subset(value)
    if not out:
        for row in periods or []:
            if not isinstance(row, Mapping):
                continue
            label = str(row.get("period_label") or row.get("label") or row.get("period") or "").strip()
            if label:
                out[label] = _metric_subset(row)
    return out


def _validation_periods(validation_metrics: Mapping[str, Mapping]) -> list[dict]:
    labels = [label for label in PERIOD_ORDER if label in validation_metrics]
    labels.extend(label for label in validation_metrics if label not in labels)
    periods: list[dict] = []
    for label in labels:
        meta = PERIOD_WINDOWS.get(label, {"start": "", "end": "", "role": "stage2_eval"})
        periods.append({"label": label, "start": meta["start"], "end": meta["end"], "role": meta["role"]})
    return periods


def _metric_subset(metrics: Mapping[str, Any]) -> dict[str, float]:
    return {key: _float(metrics.get(key)) for key in METRIC_KEYS if key in metrics}


def _normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0
