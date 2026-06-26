import json

from engine.live.central_control import (
    LiveCentralControlConfig,
    LiveCentralController,
    _adjusted_confidence_from_metrics,
)
from scripts.research.build_stage3_live_pool import (
    FilterConfig,
    build_stage3_live_pool,
    row_passes_first_filter,
    select_top_rows,
    CandidateRow,
)


def _catalog_row(ticker="AAA", rank=1, expectancy=2.0, trade_count=6, profit_factor=1.5, max_dd=-12.0, eligible=True):
    periods = {}
    for label in ("train_1", "train_2", "recent_1y"):
        periods[label] = {
            "label": label,
            "role": "pure_oos",
            "metrics": {
                "expectancy_pct": expectancy,
                "trade_count": trade_count,
                "profit_factor": profit_factor,
                "max_drawdown_pct": max_dd,
                "win_rate": 60.0,
            },
        }
    return {
        "ticker": ticker,
        "rank": rank,
        "eligible_stage3_basic": eligible,
        "rulebook_hash": f"{ticker.lower()}hash{rank}",
        "rulebook": {"ticker": ticker, "direction": "long"},
        "period_results": periods,
    }


def _candidate(row):
    return CandidateRow(
        row=row,
        source_path="sample/stage3_profile_catalog.jsonl",
        source_line=int(row["rank"]),
        ticker=row["ticker"],
        rank=int(row["rank"]),
        avg_expectancy_pct=2.0,
        avg_profit_factor=1.5,
        min_trade_count=6.0,
        worst_drawdown_pct=-12.0,
    )


def test_row_passes_first_filter_accepts_stage3_basic_rank1():
    ok, reasons = row_passes_first_filter(_catalog_row(), FilterConfig())
    assert ok is True
    assert reasons == []


def test_row_passes_first_filter_rejects_weak_pure_oos_period():
    ok, reasons = row_passes_first_filter(_catalog_row(expectancy=0.25), FilterConfig())
    assert ok is False
    assert any(reason.startswith("expectancy_below_floor") for reason in reasons)


def test_select_top_rows_keeps_rank1_per_ticker():
    cfg = FilterConfig(top_per_ticker=1, max_rank_per_ticker=3)
    rows = [_candidate(_catalog_row("AAA", rank=2)), _candidate(_catalog_row("AAA", rank=1)), _candidate(_catalog_row("BBB", rank=1))]
    selected = select_top_rows(rows, cfg)
    assert [(row.ticker, row.rank) for row in selected] == [("AAA", 1), ("BBB", 1)]


def test_build_stage3_live_pool_writes_filtered_repository(tmp_path):
    batch_root = tmp_path / "batch"
    catalog_dir = batch_root / "tickers" / "AAA" / "stage3"
    catalog_dir.mkdir(parents=True)
    rows = [_catalog_row("AAA", rank=1), _catalog_row("AAA", rank=2)]
    (catalog_dir / "stage3_profile_catalog.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    out_dir = tmp_path / "pool"
    result = build_stage3_live_pool(FilterConfig(batch_root=batch_root, out_dir=out_dir, max_rank_per_ticker=1, top_per_ticker=1))
    assert result.kept_rows == 1
    output = out_dir / "stage3_live_pool.jsonl"
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8").strip())
    assert payload["ticker"] == "AAA"
    assert payload["rank"] == 1
    assert payload["live_pool_filter"]["version"] == "stage3_live_pool_v1"


def test_stage3_mix_loader_adds_pool_tickers_outside_promoted_symbols(tmp_path):
    pool = tmp_path / "stage3_live_pool.jsonl"
    rows = [_catalog_row("AAA", rank=1), _catalog_row("ZZZ", rank=1)]
    pool.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    ctl = LiveCentralController.__new__(LiveCentralController)
    ctl.config = LiveCentralControlConfig(stage3_mix_enabled=True, stage3_live_pool_path=pool)
    ctl.confidence_mode = "adjusted"
    ctl._apply_adjusted_confidence = LiveCentralController._apply_adjusted_confidence.__get__(ctl, LiveCentralController)

    loaded = ctl._load_stage3_mix_entities()

    assert sorted(entity.ticker for entity in loaded) == ["AAA", "ZZZ"]
    assert all(entity.tags["stage"] == "stage3_live_pool" for entity in loaded)


def test_adjusted_confidence_uses_stage3_labels_when_legacy_labels_missing():
    metrics = {
        "train_1": {"trade_count": 20, "profit_factor": 2.0},
        "train_2": {"trade_count": 20, "profit_factor": 3.0},
        "recent_1y": {"trade_count": 20, "profit_factor": 4.0},
    }
    assert _adjusted_confidence_from_metrics(metrics, pf_cap=10.0, min_trades=15) == 2.0
