#!/usr/bin/env python3
"""Correct the Phase-3 selected train-fitness reporting field.

The GA result object is re-used for train/stress/OOS backtests.  Those
backtests update ``rulebook.fitness`` in place, so the initial report captured
the last evaluation value instead of the selected GA train fitness.  Candidate
selection, intervals, coverage, precision, trades, gates, and verdict are
unchanged.  The selected train fitness is recoverable exactly as the maximum
per-generation best value recorded in ``fitness_history``.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "data/_system/analysis/strict_and_interval_2sym_20260712"
SUMMARY_PATH = OUT_DIR / "summary.json"
CANDIDATE_PATH = OUT_DIR / "candidate_metrics.csv"
READOUT_PATH = OUT_DIR / "readout.md"
MANIFEST_PATH = OUT_DIR / "manifest.sha256"
REPORT_PATH = OUT_DIR / "reporting_correction.md"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def selected_train_fitness(candidate: dict) -> float:
    history = list(candidate.get("fitness_history") or [])
    values = [float(row[1]) for row in history if isinstance(row, list) and len(row) >= 2]
    if not values:
        raise RuntimeError(
            f"missing fitness history for {candidate.get('ticker')}:{candidate.get('split_label')}"
        )
    return max(values)


def main() -> int:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    table = pd.read_csv(CANDIDATE_PATH)
    corrections: list[dict] = []

    for candidate in summary.get("candidates", []):
        ticker = str(candidate["ticker"])
        split_label = str(candidate["split_label"])
        old_value = float(candidate.get("best_train_fitness", 0.0))
        new_value = selected_train_fitness(candidate)
        candidate["best_train_fitness"] = new_value
        mask = (table["ticker"] == ticker) & (table["split_label"] == split_label)
        if int(mask.sum()) != 1:
            raise RuntimeError(f"candidate row mismatch: {ticker}:{split_label}")
        table.loc[mask, "best_train_fitness"] = new_value
        corrections.append(
            {
                "ticker": ticker,
                "split_label": split_label,
                "reported_before": old_value,
                "selected_train_fitness": new_value,
                "delta": new_value - old_value,
            }
        )

    summary["reporting_correction"] = {
        "applied": True,
        "field": "best_train_fitness",
        "cause": "post-selection period backtests mutate rulebook.fitness in place",
        "source_of_truth": "max per-generation best in fitness_history",
        "selection_or_metrics_affected": False,
        "corrections": corrections,
    }

    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    table.to_csv(CANDIDATE_PATH, index=False)

    readout = READOUT_PATH.read_text(encoding="utf-8")
    note = (
        "\n## Reporting correction\n\n"
        "초기 산출물의 `best_train_fitness`는 Stress/OOS 재평가가 같은 Rulebook의 "
        "`fitness`를 갱신해 마지막 period 값으로 표시됐다. 세대별 `fitness_history`의 "
        "best 최댓값으로 선택 시점 train fitness를 복원했다. 후보 선택, interval, "
        "coverage, precision, trade 결과, gate, verdict에는 영향이 없다.\n"
    )
    if "## Reporting correction" not in readout:
        READOUT_PATH.write_text(readout.rstrip() + "\n" + note, encoding="utf-8")

    rows = [
        "# Phase 3 reporting correction",
        "",
        "`best_train_fitness` 표시값만 수정했다.",
        "",
        "| ticker | split | before | selected train fitness |",
        "|---|---|---:|---:|",
    ]
    for item in corrections:
        rows.append(
            f"| {item['ticker']} | {item['split_label']} | "
            f"{item['reported_before']:.9f} | {item['selected_train_fitness']:.9f} |"
        )
    rows.extend(
        [
            "",
            "후보 선택·구간·coverage·precision·trade·gate·verdict 변경: `NO`",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(rows), encoding="utf-8")

    manifest_lines = []
    for path in sorted(OUT_DIR.iterdir()):
        if path.name == MANIFEST_PATH.name or not path.is_file():
            continue
        manifest_lines.append(f"{sha256(path)}  {path.relative_to(ROOT)}")
    MANIFEST_PATH.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")

    print(json.dumps({"corrected": corrections}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
