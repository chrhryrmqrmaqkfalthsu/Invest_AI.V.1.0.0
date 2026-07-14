#!/usr/bin/env python3
"""Repair reporting-only fields for a completed AAP overlap-entry v4 run.

No GA, backtest, fitness, gate, strict-AND, mutation, or trade result is
recomputed.  The repair is limited to:

- filtering generation-best trade-count distributions to qualify stage only;
- syncing the readout PowerShell block with launch_command.json;
- recording the independent-event-count interpretation explicitly;
- refreshing embedded comparison objects and SHA256SUMS.txt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

FOLDS = ("train_1", "train_2", "train_3")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _qualify_generation_distribution(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold in FOLDS:
        counts = [
            int(row.get("best_trade_count", 0) or 0)
            for row in rows
            if row.get("stage") == "qualify" and row.get("fold") == fold
        ]
        if len(counts) != 40:
            raise RuntimeError(
                f"expected 40 qualify generation rows for {fold}, found {len(counts)}"
            )
        output[fold] = {
            "generation_count": len(counts),
            "min": min(counts),
            "median": statistics.median(counts),
            "max": max(counts),
            "histogram": {
                str(key): value for key, value in sorted(Counter(counts).items())
            },
        }
    return output


def _distribution_section(distribution: Mapping[str, Mapping[str, Any]]) -> str:
    lines = [
        "## Fold-best 거래수 분포",
        "",
        "### 40세대 qualify generation-best 거래수",
        "",
        "| fold | generation 수 | min | median | max | histogram |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for fold in FOLDS:
        row = distribution[fold]
        lines.append(
            f"| {fold} | {row['generation_count']} | {row['min']} | "
            f"{row['median']} | {row['max']} | "
            f"`{json.dumps(row['histogram'], ensure_ascii=False, sort_keys=True)}` |"
        )
    return "\n".join(lines)


def _event_interpretation(comparison: Mapping[str, Any]) -> str:
    support = comparison["fold_concurrency_and_support"]
    old = support["v3_single_position"]
    new = support["v4_overlap_entry"]
    lines = [
        "## 독립 사건 수 해석",
        "",
        "| fold | v3 effective event count | v4 effective event count | 변화 |",
        "|---|---:|---:|---:|",
    ]
    for fold in FOLDS:
        before = float(old[fold]["effective_event_count"])
        after = float(new[fold]["effective_event_count"])
        lines.append(f"| {fold} | {before:.6f} | {after:.6f} | {after - before:+.6f} |")
    lines.extend(
        [
            "",
            "동시진입은 세 fold의 거래수를 모두 늘렸지만 독립 사건 다양성을 일관되게 늘리지는 않았다. "
            "train_1·train_2는 같은 신호 군집 내부의 pass day가 여러 거래로 살아나면서 effective event count가 감소했고, "
            "train_3만 증가했다.",
            "",
            "따라서 이번 변경의 확인된 효과는 **보유·cooldown 흡수 제거와 거래 support 증가**다. "
            "독립 시장 사건 수 증가 효과는 fold별로 혼재되어 있다.",
            "",
            "train_3의 joint pass 30일 중 실제 거래는 13건이고 held/cooldown 흡수는 0이다. "
            "나머지 17일은 단일 포지션 제약이 아닌 기존 entry guard에서 미체결된 날짜이며, "
            "이번 산출물은 guard별 세부 사유를 별도로 분류하지 않았으므로 원인은 미확정으로 남긴다.",
        ]
    )
    return "\n".join(lines)


def _refresh_sha_manifest(out_dir: Path) -> None:
    rows: list[str] = []
    for path in sorted(out_dir.iterdir(), key=lambda value: value.name):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {path.name}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def repair(out_dir: Path) -> dict[str, Any]:
    comparison_path = out_dir / "overlap_entry_comparison.json"
    generation_path = out_dir / "generation_best_fitness.jsonl"
    launch_path = out_dir / "launch_command.json"
    readout_path = out_dir / "readout.md"
    for path in (comparison_path, generation_path, launch_path, readout_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    distribution = _qualify_generation_distribution(_read_jsonl(generation_path))
    comparison = _read_json(comparison_path)
    comparison["generation_best_trade_count_distribution"] = distribution
    comparison["generation_distribution_scope"] = "qualify_stage_only"
    _write_json(comparison_path, comparison)

    for name in ("manifest.json", "official_final_summary.json"):
        path = out_dir / name
        payload = _read_json(path)
        payload["overlap_entry_comparison"] = comparison
        payload["report_repair"] = {
            "generation_distribution_scope": "qualify_stage_only",
            "launch_readout_synced": True,
            "calculation_outputs_changed": False,
        }
        _write_json(path, payload)

    launch = _read_json(launch_path)
    powershell = str(launch.get("powershell_command") or "")
    if not powershell:
        raise RuntimeError("launch_command.json has no powershell_command")

    readout = readout_path.read_text(encoding="utf-8")
    readout = re.sub(
        r"```powershell\n.*?\n```",
        lambda _match: "```powershell\n" + powershell + "\n```",
        readout,
        count=1,
        flags=re.DOTALL,
    )
    distribution_section = _distribution_section(distribution)
    readout = re.sub(
        r"## Fold-best 거래수 분포\n.*?(?=\n### 최종 population·pass 후보 거래수와 gate 병목)",
        lambda _match: distribution_section + "\n",
        readout,
        count=1,
        flags=re.DOTALL,
    )
    event_section = _event_interpretation(comparison)
    marker = "\n## Trade-level 로그\n"
    if marker not in readout:
        raise RuntimeError("trade-level readout marker not found")
    if "\n## 독립 사건 수 해석\n" not in readout:
        readout = readout.replace(marker, "\n" + event_section + marker, 1)
    readout_path.write_text(readout, encoding="utf-8")

    _refresh_sha_manifest(out_dir)
    return {
        "generation_distribution": distribution,
        "readout_sha256": hashlib.sha256(readout_path.read_bytes()).hexdigest(),
        "sha_manifest_sha256": hashlib.sha256(
            (out_dir / "SHA256SUMS.txt").read_bytes()
        ).hexdigest(),
        "calculation_outputs_changed": False,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair overlap-entry v4 report only")
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = repair(Path(args.out_dir).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
