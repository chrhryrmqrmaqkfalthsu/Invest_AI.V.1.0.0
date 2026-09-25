import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from engine.central.entity_loader import EntityRecord
from engine.central.policy_search import EvalPeriod, SearchSettings, apply_confidence_metric, run_policy_search
from engine.central.search_space import SearchSpace
from engine.central.stage2_survivor_loader import (
    Stage2SurvivorLoaderError,
    entity_from_stage2_survivor_row,
    load_stage2_survivors,
    load_stage2_survivors_with_report,
    sell_omen_coverage_report,
)
from engine.core.indicators import calc_indicators
from engine.core.metadata import compute_rulebook_hash


BATCH_ROOT = Path("exp_batch_stage123_2009_20260616_full")
CENTRAL_INDEX = BATCH_ROOT / "central_index.jsonl"


class MemoryProvider:
    def __init__(self, frames):
        self.frames = {str(k).upper(): v.copy() for k, v in frames.items()}

    def load_price_df(self, ticker):
        return self.frames[str(ticker).upper()]

    def load_market_history(self):
        return None

    def load_ticker_sentiment(self, ticker):
        return {}


def _require_stage2_artifacts():
    if not CENTRAL_INDEX.exists():
        pytest.skip("batch Stage2 central_index is not available in this environment")


def test_load_stage2_survivors_real_artifact_mapping_and_hashes():
    _require_stage2_artifacts()
    entities = load_stage2_survivors(CENTRAL_INDEX, BATCH_ROOT, tickers=["AA"])
    assert entities
    first = entities[0]
    assert isinstance(first, EntityRecord)
    assert first.ticker == "AA"
    assert first.entity_id == f"AA_{first.rulebook_hash[:12]}"
    assert len(first.rulebook) == 88
    assert first.rulebook["ticker"] == "AA"
    assert first.rulebook["direction"] == "long"
    assert compute_rulebook_hash(first.rulebook) == first.rulebook_hash
    assert first.confidence == 0.0
    assert sorted(first.validation_metrics) == ["oos_2025h2", "stress_pre_2022h1", "train_1_eval", "train_2_eval", "train_3_eval"]
    assert {"expectancy_pct", "win_rate", "profit_factor", "trade_count", "max_drawdown_pct"}.issubset(first.validation_metrics["oos_2025h2"])
    assert first.validation_periods[-1]["label"] == "oos_2025h2"
    assert first.validation_periods[-1]["start"] == "2025-07-01"
    assert first.tags["stage"] == "stage2"
    assert first.tags["source_file"]
    assert first.source_path.endswith("survivors.jsonl")
    assert first.source_row_index > 0


def test_load_stage2_survivors_report_counts_and_ticker_filter():
    _require_stage2_artifacts()
    all_report = load_stage2_survivors_with_report(CENTRAL_INDEX, BATCH_ROOT)
    aa_report = load_stage2_survivors_with_report(CENTRAL_INDEX, BATCH_ROOT, tickers=["AA"])
    assert all_report.stage2_survivor_rows >= aa_report.loaded > 0
    assert all_report.loaded == len(all_report.entities)
    assert aa_report.loaded == len(aa_report.entities)
    assert {entity.ticker for entity in aa_report.entities} == {"AA"}
    assert aa_report.skipped_ticker_filter == all_report.stage2_survivor_rows - aa_report.loaded
    assert all_report.missing_source_files == 0
    assert all_report.unmatched_rulebook_hashes == 0
    assert all_report.skipped_hash_mismatch == 0


def test_hash_mismatch_fails_when_required_and_skips_when_not_required():
    index_row, survivor_row = _synthetic_stage2_rows()
    survivor_row_bad = copy.deepcopy(survivor_row)
    survivor_row_bad["rulebook"]["signal_threshold"] = 9.99

    with pytest.raises(Stage2SurvivorLoaderError, match="rulebook_hash mismatch"):
        entity_from_stage2_survivor_row(index_row, survivor_row_bad, source_path="synthetic", source_row_index=1)

    root, index_path = _write_stage2_artifacts_tmp(index_row, survivor_row_bad)
    report = load_stage2_survivors_with_report(index_path, root, require_hash_match=False)
    assert report.loaded == 0
    assert report.skipped_hash_mismatch == 1


def test_stage2_entityrecord_interface_matches_central_pipeline_expectations():
    _require_stage2_artifacts()
    entity = load_stage2_survivors(CENTRAL_INDEX, BATCH_ROOT, tickers=["AA"])[0]
    assert set(EntityRecord.__dataclass_fields__) == {
        "entity_id",
        "ticker",
        "rulebook",
        "rulebook_hash",
        "validation_metrics",
        "validation_periods",
        "tags",
        "confidence",
        "source_path",
        "source_row_index",
    }
    adjusted = apply_confidence_metric([entity], "expectancy")
    assert adjusted[0].confidence > 0
    adjusted_pf = apply_confidence_metric([entity], "profit_factor")
    assert adjusted_pf[0].confidence > 0
    adjusted_wr = apply_confidence_metric([entity], "win_rate")
    assert 0 < adjusted_wr[0].confidence <= 1


def test_sell_omen_coverage_report_real_artifacts():
    _require_stage2_artifacts()
    entities = load_stage2_survivors(CENTRAL_INDEX, BATCH_ROOT)
    report = sell_omen_coverage_report(entities)
    assert report.entity_count == len(entities)
    assert report.unique_tickers == len({entity.ticker for entity in entities})
    assert report.covered + report.missing == report.unique_tickers
    assert report.score_table_exists is True
    assert isinstance(report.covered_tickers, tuple)
    assert isinstance(report.missing_tickers, tuple)


def test_sell_omen_coverage_report_missing_score_table(tmp_path):
    entity = load_stage2_survivors(CENTRAL_INDEX, BATCH_ROOT, tickers=["AA"])[0] if CENTRAL_INDEX.exists() else _synthetic_entity()
    report = sell_omen_coverage_report([entity], tmp_path / "missing.csv")
    assert report.score_table_exists is False
    assert report.covered == 0
    assert report.missing == 1
    assert report.missing_tickers == (entity.ticker,)


def test_policy_search_accepts_stage2_entities_and_recomputes_confidence(tmp_path):
    _require_stage2_artifacts()
    entity = load_stage2_survivors(CENTRAL_INDEX, BATCH_ROOT, tickers=["AA"])[0]
    price = _price_df()
    space = SearchSpace(
        max_positions=[1],
        confidence_weight=[0.3],
        signal_strength_weight=[0.7],
        min_confidence=[0.0],
        confidence_metric=["expectancy"],
        position_sizing=["equal"],
    )
    result = run_policy_search(
        [entity],
        [EvalPeriod("smoke", "2025-03-03", "2025-03-14")],
        space,
        settings=SearchSettings(total_capital=10_000.0, min_trades_for_full_score=1, ledger_root=str(tmp_path / "ledgers")),
        data_provider_factory=lambda: MemoryProvider({entity.ticker: price}),
    )
    assert result.best is not None
    assert result.best.reconcile_failures == 0
    assert result.evaluated_count == 1
    assert result.best.allocation_params["min_confidence"] == 0.0


def test_loader_is_deterministic_for_same_input():
    _require_stage2_artifacts()
    first = load_stage2_survivors(CENTRAL_INDEX, BATCH_ROOT, tickers=["AA"])
    second = load_stage2_survivors(CENTRAL_INDEX, BATCH_ROOT, tickers=["AA"])
    assert first == second


def _synthetic_stage2_rows():
    rb = {
        "ticker": "AAA",
        "asset_type": "us_stock",
        "direction": "long",
        "signal_threshold": 0.0,
        "stop_loss_atr": 2.0,
        "take_profit_atr": 3.0,
        "trailing_atr": 2.0,
        "max_holding_days": 10,
        "exit_strategy": "hybrid",
        "sell_omen_enabled": False,
        "sell_omen_threshold": 1.0,
    }
    h = compute_rulebook_hash(rb)
    metrics = {
        label: {"expectancy_pct": 2.0, "win_rate": 60.0, "profit_factor": 1.6, "trade_count": 12, "max_drawdown_pct": -8.0, "fitness": 10.0}
        for label in ["train_1_eval", "train_2_eval", "train_3_eval", "stress_pre_2022h1", "oos_2025h2"]
    }
    index_row = {
        "event_type": "stage2_survivor",
        "ticker": "AAA",
        "rulebook_hash": h,
        "metrics": metrics,
        "origin_count": 1,
        "origin_train_labels": ["train_1"],
        "source_file": "tickers/AAA/stage2/survivors.jsonl",
        "source_row_index": 1,
        "artifact_paths": {"survivors": "tickers/AAA/stage2/survivors.jsonl"},
        "attempt_dir": "tickers/AAA/stage2",
        "run_id": "synthetic",
    }
    survivor_row = {
        "ticker": "AAA",
        "rulebook_hash": h,
        "rulebook": rb,
        "periods": [{"period_label": label, **values} for label, values in metrics.items()],
        "origin_count": 1,
        "origin_train_labels": ["train_1"],
        "origins": [],
    }
    return index_row, survivor_row


def _write_stage2_artifacts_tmp(index_row, survivor_row):
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="stage2_loader_test_"))
    survivor_path = root / "tickers" / "AAA" / "stage2" / "survivors.jsonl"
    survivor_path.parent.mkdir(parents=True)
    survivor_path.write_text(json.dumps(survivor_row) + "\n", encoding="utf-8")
    index_path = root / "central_index.jsonl"
    index_path.write_text(json.dumps(index_row) + "\n", encoding="utf-8")
    return root, index_path


def _synthetic_entity():
    index_row, survivor_row = _synthetic_stage2_rows()
    return entity_from_stage2_survivor_row(index_row, survivor_row)


def _price_df(days=90, start="2025-01-01", base=100.0):
    idx = pd.bdate_range(start, periods=days)
    rows = []
    for i, _ in enumerate(idx):
        close = base + i * 0.20
        rows.append({"Open": close - 0.05, "High": close + 1.0, "Low": close - 1.0, "Close": close, "Volume": 1_000_000 + i})
    return calc_indicators(pd.DataFrame(rows, index=idx))
