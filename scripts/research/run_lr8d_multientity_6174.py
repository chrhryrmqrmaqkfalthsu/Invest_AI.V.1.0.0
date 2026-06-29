#!/usr/bin/env python3
"""LR8D 6174종목 다중개체 생성 러너.

이 파일은 사용자가 요청한 "현재 LR8D16을 뽑은 방식"을 6000+ 전체 종목에
확장하기 위한 백그라운드 실행 전용 파일입니다.

무엇을 하는 파일인가:
- 기존 LR8D A+B+C+D GA 파이프라인을 그대로 재사용한다.
- 입력 universe는 data/_system/screening_universe_all.txt 의 6174개 종목이다.
- 기간은 기존 LR8D와 같은 2022 / 2023 / 2024 + 2025H2 stress 4구간이다.
- population/generations/qualified 기준도 기존 LR8D와 같다.
- 단, 최종 선별은 기존처럼 종목당 대표 개체 1개만 남기지 않는다.
- strict_k3를 통과한 ticker 안에서, stress 후보 중 기준 합격 개체를 여러 개 남긴다.
- entry 날짜 Jaccard overlap이 높은 개체는 같은 진입전략으로 간주해 중복 제거한다.

주의:
- 이 파일은 research artifact만 쓴다.
- data/symbols/parameters.json을 수정하지 않는다.
- live runner를 시작하거나 중지하지 않는다.
- output은 data/_system/research/lr8d_multientity_6174_20260630/ 아래에 생성된다.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research import run_lr8c_run2_fulluniverse as runner
from engine.pipeline.topn_survivor import score_topn_validation_periods

RUN_ID = "lr8d_multientity_6174_20260630"
RUN_PREFIX = "lr8d_multi6174"
DEFAULT_TICKER_FILE = Path("data/_system/screening_universe_all.txt")
OUT_DIR = Path(f"data/_system/research/{RUN_ID}")
README_PATH = OUT_DIR / "README.md"
MULTI_ENTITY_PATH = OUT_DIR / f"{RUN_PREFIX}_multi_entity_candidates.jsonl"
MULTI_ENTITY_MANIFEST_PATH = OUT_DIR / f"{RUN_PREFIX}_multi_entity_manifest.json"
MULTI_ENTITY_REPORT_PATH = OUT_DIR / f"{RUN_PREFIX}_MULTI_ENTITY_REPORT.md"
ENTRY_OVERLAP_THRESHOLD = float(os.environ.get("LR8D_MULTI_ENTRY_OVERLAP_THRESHOLD", "0.70"))
MAX_ENTITIES_PER_TICKER = int(os.environ.get("LR8D_MULTI_MAX_ENTITIES_PER_TICKER", "5"))
MIN_ENTITY_EXPECTANCY_PCT = float(os.environ.get("LR8D_MULTI_MIN_ENTITY_EXPECTANCY_PCT", "1.0"))
DD_CUTOFF = float(os.environ.get("LR8D_MULTI_DD_CUTOFF", "-25.0"))

runner.OUT_DIR = OUT_DIR
runner.TIMING_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_timing.txt"
runner.TOPN_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_topn.jsonl"
runner.RULEBOOKS_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_topn_rulebooks.jsonl"
runner.TRADES_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_trades.jsonl"
runner.SURVIVORS_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_ticker_level_survivors.jsonl"
runner.REPORT_PATH = runner.OUT_DIR / f"{RUN_PREFIX}_BASE_TICKER_SURVIVOR_REPORT.md"


def _read_ticker_file(path: str | Path | None = None) -> tuple[str, ...]:
    ticker_path = Path(path or os.environ.get("LR8D_MULTI_TICKER_FILE", str(DEFAULT_TICKER_FILE)))
    symbols: list[str] = []
    seen: set[str] = set()
    for line in ticker_path.read_text(encoding="utf-8").splitlines():
        ticker = line.strip().upper()
        if not ticker or ticker.startswith("#"):
            continue
        if ticker not in seen:
            seen.add(ticker)
            symbols.append(ticker)
    if not symbols:
        raise RuntimeError(f"ticker file has no symbols: {ticker_path}")
    return tuple(symbols)


def _load_6174_universe(config=None):
    """Monkey-patched universe loader for the imported LR8D runner.

    The original LR8D runner loaded promoted live symbols.  This wrapper replaces
    that with the explicit 6174 ticker file while keeping the rest of the LR8D
    training/evaluation code untouched.
    """
    symbols = _read_ticker_file()
    return SimpleNamespace(
        symbols=symbols,
        config=config,
        summary=lambda: {
            "source": str(os.environ.get("LR8D_MULTI_TICKER_FILE", DEFAULT_TICKER_FILE)),
            "symbol_count": len(symbols),
            "runner": RUN_ID,
        },
    )


runner.load_live_universe = _load_6174_universe


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _candidate_sort_key(row: Mapping[str, Any]) -> tuple[float, float, float, float, int]:
    metrics = row.get("oos_metrics") if isinstance(row.get("oos_metrics"), Mapping) else {}
    return (
        _safe_float(metrics.get("expectancy_pct"), -999.0),
        _safe_float(metrics.get("profit_factor"), -999.0),
        _safe_float(row.get("oos_member_score"), -999.0),
        -abs(_safe_float(metrics.get("max_drawdown_pct"), -999.0)),
        -_safe_int(row.get("rank_is"), 999999),
    )


def _entry_dates_from_trade_row(row: Mapping[str, Any]) -> set[str]:
    dates: set[str] = set()
    for trade in row.get("trades") or []:
        if not isinstance(trade, Mapping):
            continue
        raw = trade.get("entry_date") or trade.get("entry_time") or trade.get("entry_dt") or trade.get("entry_signal_date")
        if raw is not None:
            dates.add(str(raw)[:10])
    return dates


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return float(len(a & b) / len(union))


def _load_rulebooks_by_hash() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in _load_jsonl(runner.RULEBOOKS_PATH):
        h = str(row.get("rulebook_hash") or "")
        rb = row.get("rulebook")
        if h and isinstance(rb, dict):
            out.setdefault(h, dict(rb))
    return out


def _load_trade_dates_by_ticker_hash() -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for row in _load_jsonl(runner.TRADES_PATH):
        ticker = str(row.get("ticker") or "").upper().strip()
        h = str(row.get("rulebook_hash") or "").strip()
        if not ticker or not h:
            continue
        dates = _entry_dates_from_trade_row(row)
        if dates:
            out.setdefault((ticker, h), set()).update(dates)
    return out


def _ticker_level_strict_passes(topn_validation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = runner.survivor_rows_with_ticker(topn_validation, runner.STRICT_K)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        dd = _safe_float(row.get("worst_drawdown_pct"), 0.0)
        stress = _safe_float(row.get("stress_worst_expectancy_pct"), -999.0)
        if dd <= DD_CUTOFF:
            continue
        if stress < 0.0:
            continue
        out[ticker] = dict(row)
    return out


def _stress_candidates_by_ticker(topn_validation: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    scored = score_topn_validation_periods(
        topn_validation,
        general_years=runner.GENERAL_YEARS,
        stress_labels=(runner.STRESS_LABEL,),
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for period in scored.get("stress_periods", []):
        period_ticker = str(period.get("ticker") or "").upper().strip()
        for candidate in period.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            ticker = str(candidate.get("ticker") or period_ticker).upper().strip()
            metrics = candidate.get("oos_metrics") if isinstance(candidate.get("oos_metrics"), Mapping) else {}
            if _safe_int(metrics.get("trade_count"), 0) < runner.MIN_TRADES:
                continue
            if _safe_float(candidate.get("oos_member_score"), 0.0) < runner.MIN_MEMBER_SCORE:
                continue
            if _safe_float(metrics.get("expectancy_pct"), 0.0) < MIN_ENTITY_EXPECTANCY_PCT:
                continue
            if _safe_float(metrics.get("max_drawdown_pct"), 0.0) <= DD_CUTOFF:
                continue
            row = dict(candidate)
            row["ticker"] = ticker
            row["period_label"] = str(period.get("label") or runner.STRESS_LABEL)
            out.setdefault(ticker, []).append(row)
    for ticker in out:
        out[ticker].sort(key=_candidate_sort_key, reverse=True)
    return out


def build_multi_entity_selection() -> dict[str, Any]:
    """Build final multi-entity candidates after all shard rows available.

    Selection policy:
    1. Ticker must pass the old strict_k3 ticker-level gate plus DD/stress filters.
    2. Candidate must be a qualified 2025H2 stress candidate.
    3. Candidate must satisfy min expectancy, min trades, min member score, DD cutoff.
    4. Within a ticker, candidates whose entry-date Jaccard overlap is >= 0.70 are
       considered duplicate entry strategies; only the better-scored one is kept.
    5. Up to MAX_ENTITIES_PER_TICKER non-duplicate candidates are kept.
    """
    topn_rows = _load_jsonl(runner.TOPN_PATH)
    topn_validation = runner.load_topn_validation_from_rows(topn_rows)
    ticker_passes = _ticker_level_strict_passes(topn_validation)
    stress_candidates = _stress_candidates_by_ticker(topn_validation)
    rulebooks_by_hash = _load_rulebooks_by_hash()
    entry_dates_by_key = _load_trade_dates_by_ticker_hash()

    selected_rows: list[dict[str, Any]] = []
    duplicate_rows: list[dict[str, Any]] = []
    missing_rulebook_count = 0
    for ticker in sorted(ticker_passes):
        kept_for_ticker: list[dict[str, Any]] = []
        kept_entry_dates: list[set[str]] = []
        for candidate in stress_candidates.get(ticker, []):
            h = str(candidate.get("rulebook_hash") or "")
            rb = rulebooks_by_hash.get(h)
            if rb is None:
                missing_rulebook_count += 1
                continue
            entry_dates = entry_dates_by_key.get((ticker, h), set())
            max_overlap = max((_jaccard(entry_dates, prior) for prior in kept_entry_dates), default=0.0)
            if max_overlap >= ENTRY_OVERLAP_THRESHOLD:
                duplicate_rows.append(
                    {
                        "ticker": ticker,
                        "rulebook_hash": h,
                        "reason": "duplicate_entry_strategy",
                        "max_entry_date_jaccard": round(max_overlap, 6),
                        "entry_date_count": len(entry_dates),
                    }
                )
                continue
            metrics = candidate.get("oos_metrics") if isinstance(candidate.get("oos_metrics"), Mapping) else {}
            survivor = ticker_passes[ticker]
            row = {
                "_comment": "LR8D 6174 다중개체 최종 후보. strict_k3 ticker gate 통과 후 2025H2 stress 후보 중 entry-date 중복을 제거해 남긴 개체입니다.",
                "run_id": RUN_ID,
                "ticker": ticker,
                "rulebook_hash": h,
                "member_hash": h,
                "source": "lr8d_multientity_strict_k3_stress_non_duplicate_entry",
                "source_period_label": candidate.get("period_label"),
                "rank_is": candidate.get("rank_is"),
                "oos_member_score": candidate.get("oos_member_score"),
                "oos_metrics": metrics,
                "entry_date_count": len(entry_dates),
                "entry_date_sample": sorted(entry_dates)[:20],
                "max_entry_date_jaccard_vs_kept": round(max_overlap, 6),
                "ticker_gate": {
                    "combo_id": "strict_k3",
                    "eligible_years": survivor.get("eligible_years"),
                    "eligible_year_count": survivor.get("eligible_year_count"),
                    "avg_expectancy_pct": survivor.get("avg_expectancy_pct"),
                    "min_expectancy_pct": survivor.get("min_expectancy_pct"),
                    "worst_drawdown_pct": survivor.get("worst_drawdown_pct"),
                    "stress_worst_expectancy_pct": survivor.get("stress_worst_expectancy_pct"),
                },
                "selection_filter": {
                    "ticker_level_combo_id": "strict_k3",
                    "dd_cutoff_gt": DD_CUTOFF,
                    "stress_worst_expectancy_gte": 0.0,
                    "candidate_period": runner.STRESS_LABEL,
                    "min_trades": runner.MIN_TRADES,
                    "min_member_score": runner.MIN_MEMBER_SCORE,
                    "min_entity_expectancy_pct": MIN_ENTITY_EXPECTANCY_PCT,
                    "entry_overlap_threshold_jaccard_lt": ENTRY_OVERLAP_THRESHOLD,
                    "max_entities_per_ticker": MAX_ENTITIES_PER_TICKER,
                },
                "rulebook": rb,
            }
            kept_for_ticker.append(row)
            kept_entry_dates.append(set(entry_dates))
            selected_rows.append(row)
            if len(kept_for_ticker) >= MAX_ENTITIES_PER_TICKER:
                break

    selected_rows.sort(
        key=lambda row: (
            row["ticker"],
            -_safe_float((row.get("oos_metrics") or {}).get("expectancy_pct"), 0.0),
            -_safe_float(row.get("oos_member_score"), 0.0),
            str(row.get("rulebook_hash") or ""),
        )
    )
    _append_jsonl_rows(MULTI_ENTITY_PATH, selected_rows)

    counts_by_ticker = Counter(row["ticker"] for row in selected_rows)
    exp_values = [_safe_float((row.get("oos_metrics") or {}).get("expectancy_pct"), 0.0) for row in selected_rows]
    manifest = {
        "_comment": "LR8D 6174 다중개체 run의 최종 선별 manifest입니다. JSON은 주석을 지원하지 않으므로 설명은 _comment 필드에 저장했습니다.",
        "run_id": RUN_ID,
        "run_prefix": RUN_PREFIX,
        "ticker_file": str(os.environ.get("LR8D_MULTI_TICKER_FILE", DEFAULT_TICKER_FILE)),
        "input_ticker_count": len(_read_ticker_file()),
        "period_rows_done": len(topn_rows),
        "expected_period_rows": len(_read_ticker_file()) * 4,
        "base_lr8d_config": {
            "population": runner.POPULATION,
            "generations": runner.GENERATIONS,
            "qualified_collect_n": runner.QUALIFIED_COLLECT_N,
            "max_candidates_per_period": runner.MAX_CANDIDATES_PER_PERIOD,
            "min_trades": runner.MIN_TRADES,
            "min_member_score": runner.MIN_MEMBER_SCORE,
            "general_years": list(runner.GENERAL_YEARS),
            "stress_label": runner.STRESS_LABEL,
        },
        "multi_entity_filter": {
            "ticker_level_gate": "strict_k3 plus worst_drawdown_pct > DD_CUTOFF and stress_worst_expectancy_pct >= 0",
            "candidate_source": "qualified stress-period candidates",
            "min_entity_expectancy_pct": MIN_ENTITY_EXPECTANCY_PCT,
            "entry_overlap_threshold_jaccard": ENTRY_OVERLAP_THRESHOLD,
            "max_entities_per_ticker": MAX_ENTITIES_PER_TICKER,
            "dd_cutoff": DD_CUTOFF,
        },
        "counts": {
            "strict_ticker_pass_count": len(ticker_passes),
            "selected_entity_count": len(selected_rows),
            "selected_ticker_count": len(counts_by_ticker),
            "duplicate_entry_strategy_rejected": len(duplicate_rows),
            "missing_rulebook_count": missing_rulebook_count,
        },
        "selected_entities_per_ticker_distribution": dict(sorted(Counter(counts_by_ticker.values()).items())),
        "expectancy_pct_summary": {
            "min": min(exp_values) if exp_values else None,
            "avg": mean(exp_values) if exp_values else None,
            "max": max(exp_values) if exp_values else None,
        },
        "outputs": {
            "multi_entity_candidates": str(MULTI_ENTITY_PATH),
            "ticker_level_survivors": str(runner.SURVIVORS_PATH),
            "topn": str(runner.TOPN_PATH),
            "rulebooks": str(runner.RULEBOOKS_PATH),
            "trades": str(runner.TRADES_PATH),
            "report": str(MULTI_ENTITY_REPORT_PATH),
        },
    }
    _write_json(MULTI_ENTITY_MANIFEST_PATH, manifest)
    return manifest


def write_readme() -> None:
    README_PATH.parent.mkdir(parents=True, exist_ok=True)
    if README_PATH.exists():
        return
    README_PATH.write_text(
        "# LR8D 6174 Multi-Entity Run\n\n"
        "이 폴더는 6174개 전체 ticker를 대상으로 기존 LR8D A+B+C+D 방식의 GA를 실행하고,\n"
        "최종 단계에서 종목당 하나만 남기지 않고 중복 진입전략을 제거한 다중 개체 후보를 저장하는 research output입니다.\n\n"
        "주요 파일:\n"
        f"- `{RUN_PREFIX}_topn.jsonl`: ticker/period별 qualified 후보 묶음\n"
        f"- `{RUN_PREFIX}_topn_rulebooks.jsonl`: qualified 후보 rulebook 전문\n"
        f"- `{RUN_PREFIX}_trades.jsonl`: qualified 후보 OOS trade dump\n"
        f"- `{RUN_PREFIX}_ticker_level_survivors.jsonl`: 기존 LR8D식 ticker-level survivor\n"
        f"- `{RUN_PREFIX}_multi_entity_candidates.jsonl`: 최종 다중개체 후보. strict_k3 통과 ticker 안에서 entry-date 중복을 제거한 개체 목록\n"
        f"- `{RUN_PREFIX}_multi_entity_manifest.json`: run 설정과 최종 후보 수 요약\n\n"
        "주의: 이 폴더는 live parameters를 수정하지 않는 research artifact입니다.\n",
        encoding="utf-8",
    )


_ORIGINAL_WRITE_REPORT = runner.write_survivors_and_report


def write_survivors_and_report(universe_symbols, timing):
    """Write original ticker-level artifacts plus the requested multi-entity outputs."""
    write_readme()
    _ORIGINAL_WRITE_REPORT(universe_symbols, timing)
    manifest = build_multi_entity_selection()
    report = f"""# LR8D 6174 Multi-Entity Report

이 파일은 6174개 전체 ticker 대상 LR8D 다중개체 선별 결과 보고서입니다.

## 목적

기존 LR8D16 export는 strict_k3 통과 ticker에서 대표 rulebook 1개만 live universe로 내보냈습니다.
이번 run은 같은 LR8D 생성 방식을 쓰되, 통과 ticker 안에서 기준 합격 개체를 여러 개 보존하고,
entry-date Jaccard overlap으로 중복 진입전략을 제거합니다.

## 최종 설정

```json
{json.dumps(manifest.get('multi_entity_filter', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

## 카운트

```json
{json.dumps(manifest.get('counts', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

## 출력 파일

```json
{json.dumps(manifest.get('outputs', {}), ensure_ascii=False, indent=2, sort_keys=True)}
```

## 주의

이 run은 research output만 생성합니다. `data/symbols/parameters.json` 또는 live runner 상태를 직접 변경하지 않습니다.
"""
    MULTI_ENTITY_REPORT_PATH.write_text(report, encoding="utf-8")


runner.write_survivors_and_report = write_survivors_and_report


def main() -> int:
    write_readme()
    return runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
