from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.universe import LiveUniverseConfig, load_live_universe  # noqa: E402
from scripts.live.export_lr8d_stage1_universe import (  # noqa: E402
    DEFAULT_PROMOTION_ID,
    build_stage1_selection,
    export_stage1,
)

RUN_DIR = ROOT / "data/_system/research/lr8d_abcd_20260608"


def _write_minimal_existing_parameters(symbols_dir: Path, tickers: list[str]) -> None:
    for ticker in tickers:
        d = symbols_dir / ticker
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "test_existing",
            "saved_at": "2026-06-09T00:00:00Z",
            "asset_meta": {
                "ticker": ticker,
                "asset_type": "us_stock",
                "currency": "USD",
                "direction": "long",
                "market": "NYSE/NASDAQ",
                "name": ticker,
                "trading_hours": {
                    "timezone": "America/New_York",
                    "open": "09:30",
                    "close": "16:00",
                    "pre_auction_end": None,
                    "post_auction_start": None,
                },
            },
            "rulebook": {},
            "promotion": {"promotion_id": "old"},
        }
        (d / "parameters.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_stage1_selection_is_expected_16_tickers():
    selections = build_stage1_selection(RUN_DIR)
    tickers = [s.ticker for s in selections]
    assert len(selections) == 16
    assert tickers == sorted(tickers)
    assert tickers == [
        "CAKE",
        "CRWD",
        "CW",
        "EME",
        "ETR",
        "HSBC",
        "ITT",
        "KT",
        "LASR",
        "MPC",
        "MPLX",
        "MTB",
        "NBIX",
        "WAB",
        "WELL",
        "WPM",
    ]
    assert all(s.survivor["combo_id"] == "strict_k3" for s in selections)
    assert all(float(s.survivor["worst_drawdown_pct"]) > -25 for s in selections)
    assert all(float(s.survivor["stress_worst_expectancy_pct"]) >= 0 for s in selections)


def test_export_stage1_apply_writes_promoted_live_universe_and_backups_in_temp_dir(tmp_path: Path):
    selections = build_stage1_selection(RUN_DIR)
    tickers = [s.ticker for s in selections]
    symbols_dir = tmp_path / "symbols"
    backup_root = tmp_path / "backups"
    _write_minimal_existing_parameters(symbols_dir, tickers)

    manifest = export_stage1(
        run_dir=RUN_DIR,
        symbols_dir=symbols_dir,
        promotion_id=DEFAULT_PROMOTION_ID,
        apply=True,
        confirm_promotion_id=DEFAULT_PROMOTION_ID,
        manifest_path=tmp_path / "manifest.json",
        backup_root=backup_root,
    )

    assert manifest["count"] == 16
    assert manifest["tickers"] == tickers
    assert manifest["backup_dir"]
    assert (tmp_path / "manifest.json").exists()

    backup_dir = Path(manifest["backup_dir"])
    assert backup_dir.is_dir()
    for ticker in tickers:
        backup_file = backup_dir / ticker / "parameters.json"
        assert backup_file.exists()
        backup = json.loads(backup_file.read_text(encoding="utf-8"))
        assert backup["promotion"]["promotion_id"] == "old"

    universe = load_live_universe(
        LiveUniverseConfig(
            market="US",
            universe_mode="promoted",
            promotion_id=DEFAULT_PROMOTION_ID,
            symbols_dir=symbols_dir,
        )
    )
    assert list(universe.symbols) == tickers
    assert universe.excluded_reason_counts == {}

    crwd = json.loads((symbols_dir / "CRWD" / "parameters.json").read_text(encoding="utf-8"))
    assert crwd["promotion"]["promotion_id"] == DEFAULT_PROMOTION_ID
    assert crwd["promotion"]["selection_filter"]["combo_id"] == "strict_k3"
    assert crwd["rulebook"]["ticker"] == "CRWD"
    assert crwd["rulebook"]["direction"] == "long"
    assert crwd["asset_meta"]["market"] == "NYSE/NASDAQ"


def test_export_stage1_refuses_apply_without_exact_confirmation_and_without_backup(tmp_path: Path):
    selections = build_stage1_selection(RUN_DIR)
    symbols_dir = tmp_path / "symbols"
    backup_root = tmp_path / "backups"
    _write_minimal_existing_parameters(symbols_dir, [s.ticker for s in selections])

    try:
        export_stage1(
            run_dir=RUN_DIR,
            symbols_dir=symbols_dir,
            promotion_id=DEFAULT_PROMOTION_ID,
            apply=True,
            confirm_promotion_id="wrong",
            manifest_path=tmp_path / "manifest.json",
            backup_root=backup_root,
        )
    except RuntimeError as exc:
        assert "confirm-promotion-id" in str(exc)
    else:
        raise AssertionError("export_stage1 must refuse unconfirmed writes")

    assert not (tmp_path / "manifest.json").exists()
    assert not backup_root.exists()
