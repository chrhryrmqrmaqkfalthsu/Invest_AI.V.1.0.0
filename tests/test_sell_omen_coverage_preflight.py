from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research.sell_omen_coverage_preflight import build_report, parse_args  # noqa: E402


def _write_score_table(path: Path) -> None:
    path.write_text(
        "ticker,Date,sell_omen_score,model_train_end,score_year\n"
        "AAA,2024-01-02,0.40,2023-12-31,2024\n"
        "AAA,2024-01-03,0.60,2023-12-31,2024\n"
        "BBB,2024-01-02,0.80,2023-12-31,2024\n",
        encoding="utf-8",
    )


def _write_ticker_file(path: Path) -> None:
    path.write_text("AAA\nBBB\nCCC\n", encoding="utf-8")


def _write_survivors(path: Path) -> None:
    rows = [
        {"ticker": "AAA", "combo_id": "strict_k3"},
        {"ticker": "CCC", "combo_id": "strict_k3"},
        {"ticker": "BBB", "combo_id": "balanced_k2"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _write_symbols(symbols_dir: Path) -> None:
    for ticker, promotion_id in [("AAA", "p1"), ("BBB", "old"), ("CCC", "p1")]:
        d = symbols_dir / ticker
        d.mkdir(parents=True, exist_ok=True)
        (d / "parameters.json").write_text(
            json.dumps({"promotion": {"promotion_id": promotion_id}}),
            encoding="utf-8",
        )


def _write_trades(path: Path) -> None:
    rows = [
        {
            "ticker": "AAA",
            "trades": [
                {"holding_path_full": [{"date": "2024-01-02"}, {"date": "2024-01-03"}]},
            ],
        },
        {
            "ticker": "CCC",
            "trades": [
                {"holding_path_full": [{"date": "2024-01-02"}]},
            ],
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _args(tmp_path: Path, *, trade_date_gate: str = "warn"):
    score = tmp_path / "scores.csv"
    target = tmp_path / "target.txt"
    survivors = tmp_path / "survivors.jsonl"
    trades = tmp_path / "trades.jsonl"
    symbols = tmp_path / "symbols"
    _write_score_table(score)
    _write_ticker_file(target)
    _write_survivors(survivors)
    _write_symbols(symbols)
    _write_trades(trades)
    return parse_args(
        [
            "--score-table",
            str(score),
            "--target-tickers",
            str(target),
            "--survivors",
            str(survivors),
            "--trades",
            str(trades),
            "--symbols-dir",
            str(symbols),
            "--stage1-promotion-id",
            "p1",
            "--years",
            "2024",
            "--trade-date-gate",
            trade_date_gate,
        ]
    )


def test_preflight_reports_ticker_and_trade_coverage_failures(tmp_path: Path):
    report, failures, warnings = build_report(_args(tmp_path))

    assert report["score_summary"]["rows"] == 3
    coverage = {row["name"]: row for row in report["coverage"]}
    assert coverage["target"]["covered"] == 2
    assert coverage["target"]["total"] == 3
    assert coverage["target"]["missing_sample"] == ["CCC"]
    assert coverage["stage1"]["covered"] == 1
    assert coverage["stage1"]["total"] == 2
    assert coverage["survivors"]["covered"] == 2
    assert coverage["survivors"]["total"] == 3
    assert coverage["strict_k3"]["covered"] == 1
    assert coverage["strict_k3"]["total"] == 2

    trade = report["trade_date_coverage"]
    assert trade["observations"] == 3
    assert trade["matched_observations"] == 2
    assert any("target ticker coverage" in msg for msg in failures)
    assert any("stage1 ticker coverage" in msg for msg in failures)
    assert any("survivors ticker coverage" in msg for msg in failures)
    assert any("strict_k3 ticker coverage" in msg for msg in failures)
    assert any("trade ticker_date coverage" in msg for msg in warnings)
    assert report["ok"] is False


def test_trade_date_gate_can_fail_the_run(tmp_path: Path):
    report, failures, warnings = build_report(_args(tmp_path, trade_date_gate="fail"))
    assert any("trade ticker_date coverage" in msg for msg in failures)
    assert not any("trade ticker_date coverage" in msg for msg in warnings)
    assert report["ok"] is False
