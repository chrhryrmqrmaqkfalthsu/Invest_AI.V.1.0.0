#!/usr/bin/env python3
"""LR8D Stage1 통과 종목 다중개체 early-prune robust launcher.

이 파일은 6174개 전체 universe 대신 기존 honest Stage1 필터를 통과한
2009개 종목만 대상으로 LR8D 다중개체 생성을 실행하는 백그라운드 런처입니다.

무엇을 하는 파일인가:
- 입력 ticker 파일: data/_system/research/honest_stage1_pass_tickers_20260616_final.txt
- 기존 LR8D A+B+C+D GA 생성 방식은 그대로 사용한다.
- 기존 LR8D16과 같은 2022 / 2023 / 2024 / 2025H2 4구간 구조를 쓴다.
- 한 구간이라도 통과 기준을 만족하는 후보가 없으면 그 ticker의 남은 구간은 즉시 스킵한다.
- 구간 통과 기준은 trade_count >= 5, member_score >= 10, expectancy >= 1%, max_drawdown > -25%이다.
- 최종 export만 종목당 1개 제한이 아니라, 기준 합격 + entry-date 중복 제거 후
  ticker당 최대 5개 개체를 남기는 방식이다.
- output은 data/_system/research/lr8d_multientity_stage1pass_pruned_20260630/ 아래에 따로 쓴다.
- 기존 6174 전체 run 또는 기존 stage1pass non-pruned output과 절대 섞지 않는다.
- shard 실행은 TOPN/RULEBOOKS/TRADES append만 수행한다.
- 모든 shard 완료 후 이 파일을 --finalize-only로 1회 실행하면 survivor/multi/report를 단일 aggregation으로 생성한다.

주의:
- 이 파일은 research artifact만 생성한다.
- data/symbols/parameters.json을 수정하지 않는다.
- live runner 또는 broker 주문 상태를 변경하지 않는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research import run_lr8d_multientity_6174 as base

RUN_ID = "lr8d_multientity_stage1pass_pruned_20260630"
RUN_PREFIX = "lr8d_stage1pass_pruned"
TICKER_FILE = Path("data/_system/research/honest_stage1_pass_tickers_20260616_final.txt")
OUT_DIR = Path(f"data/_system/research/{RUN_ID}")

# Early-prune defaults for this Stage1-pass run.  The common robust runner reads
# these env values at import time, so set them before importing it.
os.environ.setdefault("LR8D_MULTI_EARLY_PRUNE", "1")
os.environ.setdefault("LR8D_MULTI_EARLY_PRUNE_MIN_EXPECTANCY_PCT", "1.0")
os.environ.setdefault("LR8D_MULTI_EARLY_PRUNE_DD_CUTOFF", "-25.0")
# Final survivor/multi/report는 shard별 동시쓰기 race를 피하려고 기본적으로
# shard 종료 시 쓰지 않는다. 모든 shard 완료 후 --finalize-only로 1회 실행한다.
os.environ.setdefault("LR8D_MULTI_FINALIZE_ON_SHARD_COMPLETE", "0")

# Patch the reusable LR8D multi-entity module before importing its robust runner.
# The imported module keeps all LR8D generation logic identical, but these globals
# redirect input/output to the Stage1-pass universe and a separate pruned artifact folder.
base.RUN_ID = RUN_ID
base.RUN_PREFIX = RUN_PREFIX
base.DEFAULT_TICKER_FILE = TICKER_FILE
base.OUT_DIR = OUT_DIR
base.README_PATH = OUT_DIR / "README.md"
base.MULTI_ENTITY_PATH = OUT_DIR / f"{RUN_PREFIX}_multi_entity_candidates.jsonl"
base.MULTI_ENTITY_MANIFEST_PATH = OUT_DIR / f"{RUN_PREFIX}_multi_entity_manifest.json"
base.MULTI_ENTITY_REPORT_PATH = OUT_DIR / f"{RUN_PREFIX}_MULTI_ENTITY_REPORT.md"
base.runner.OUT_DIR = OUT_DIR
base.runner.TIMING_PATH = OUT_DIR / f"{RUN_PREFIX}_timing.txt"
base.runner.TOPN_PATH = OUT_DIR / f"{RUN_PREFIX}_topn.jsonl"
base.runner.RULEBOOKS_PATH = OUT_DIR / f"{RUN_PREFIX}_topn_rulebooks.jsonl"
base.runner.TRADES_PATH = OUT_DIR / f"{RUN_PREFIX}_trades.jsonl"
base.runner.SURVIVORS_PATH = OUT_DIR / f"{RUN_PREFIX}_ticker_level_survivors.jsonl"
base.runner.REPORT_PATH = OUT_DIR / f"{RUN_PREFIX}_BASE_TICKER_SURVIVOR_REPORT.md"

from scripts.research import run_lr8d_multientity_6174_robust as robust  # noqa: E402

# Rebind robust artifact paths after the patch above.  JSONL files also include
# _comment fields explaining what they are, so downstream review is unambiguous.
robust.base = base
robust.runner = base.runner
robust.FAILURES_PATH = OUT_DIR / f"{RUN_PREFIX}_failures.jsonl"
robust.PROGRESS_PATH = OUT_DIR / f"{RUN_PREFIX}_progress.json"
robust.PRUNED_PATH = OUT_DIR / f"{RUN_PREFIX}_pruned_tickers.jsonl"
robust.FINALIZE_LOCK_PATH = OUT_DIR / f"{RUN_PREFIX}_finalize.lock"
robust.EARLY_PRUNE_ENABLED = True
robust.EARLY_PRUNE_MIN_EXPECTANCY_PCT = 1.0
robust.EARLY_PRUNE_DD_CUTOFF = -25.0
robust.FINALIZE_ON_SHARD_COMPLETE = False


def _write_stage1pass_readme() -> None:
    """Write a run-specific README so Stage1-pass output has no 6174 wording."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "README.md").write_text(
        "# LR8D Stage1-Pass Pruned Multi-Entity Run\n\n"
        "이 폴더는 `honest_stage1_pass_tickers_20260616_final.txt`의 Stage1 통과 종목만 대상으로 "
        "기존 LR8D A+B+C+D 방식의 GA를 실행한 research output입니다.\n\n"
        "## 생성 방식\n\n"
        "- 기간 구조: 2022 / 2023 / 2024 / 2025H2 stress\n"
        "- 한 구간이라도 pass 후보가 없으면 해당 ticker의 남은 구간을 즉시 스킵합니다.\n"
        "- 구간 pass gate: trade_count >= 5, oos_member_score >= 10, expectancy_pct >= 1.0, max_drawdown_pct > -25.0\n"
        "- 최종 단계에서는 strict_k3 ticker gate 통과 후 2025H2 stress 후보 중 entry-date Jaccard 중복을 제거해 ticker당 최대 5개를 남깁니다.\n\n"
        "## 주요 파일\n\n"
        f"- `{RUN_PREFIX}_topn.jsonl`: ticker/period별 qualified 후보 묶음\n"
        f"- `{RUN_PREFIX}_topn_rulebooks.jsonl`: qualified 후보 rulebook 전문\n"
        f"- `{RUN_PREFIX}_trades.jsonl`: qualified 후보 OOS trade dump\n"
        f"- `{RUN_PREFIX}_pruned_tickers.jsonl`: early-prune으로 버린 ticker/period 감사 로그\n"
        f"- `{RUN_PREFIX}_failures.jsonl`: ticker/period 실패 감사 로그\n"
        f"- `{RUN_PREFIX}_ticker_level_survivors.jsonl`: finalize 후 생성되는 기존 LR8D식 ticker-level survivor\n"
        f"- `{RUN_PREFIX}_multi_entity_candidates.jsonl`: finalize 후 생성되는 최종 다중개체 후보\n"
        f"- `{RUN_PREFIX}_multi_entity_manifest.json`: finalize 후 생성되는 run 설정과 최종 후보 수 요약\n\n"
        "## 운영 절차\n\n"
        "shard 실행 중에는 TOPN/RULEBOOKS/TRADES/PRUNED/FAILURES만 append됩니다. "
        "모든 shard가 종료된 뒤 다음 명령을 1회 실행해 survivor/multi/report를 생성합니다.\n\n"
        "```bash\n"
        "cd ~/kingmaker && venv/bin/python scripts/research/run_lr8d_multientity_stage1pass_robust.py --finalize-only\n"
        "```\n\n"
        "주의: 이 폴더는 live parameters를 수정하지 않는 research artifact입니다.\n",
        encoding="utf-8",
    )


base.write_readme = _write_stage1pass_readme


def main() -> int:
    return robust.main()


if __name__ == "__main__":
    raise SystemExit(main())
