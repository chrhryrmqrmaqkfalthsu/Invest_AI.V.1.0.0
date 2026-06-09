"""Point-in-time universe manifest builder for LR8D bias probe.

This module only builds and validates PIT universe manifests from the existing
LR8D top-N OOS output. It intentionally does not rerun GA/backtests and does not
simulate portfolio trades.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

TOPN_PATH = Path("data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn.jsonl")
OUT_DIR = Path("data/_system/research/central_portfolio/pit_universe_bias_probe")
PARAMS_PATH_TMPL = "data/symbols/{ticker}/parameters.json"
SELECTION_RULE_ID = "pit_all_available_labels_top16_v0"
MIN_TRADES = 5
MIN_MEMBER_SCORE = 10.0
MIN_EXPECTANCY_PCT = 1.0
PRIMARY_TOP_N = 16


@dataclass(frozen=True)
class AsOfSpec:
    as_of_date: str
    trade_start_date: str
    trade_end_date: str
    allowed_labels: tuple[str, ...]
    forbidden_labels: tuple[str, ...]


AS_OF_SPECS: tuple[AsOfSpec, ...] = (
    AsOfSpec(
        as_of_date="2023-12-31",
        trade_start_date="2024-01-01",
        trade_end_date="2024-12-31",
        allowed_labels=("2022", "2023"),
        forbidden_labels=("2024", "2025H2"),
    ),
    AsOfSpec(
        as_of_date="2024-12-31",
        trade_start_date="2025-01-01",
        trade_end_date="2025-12-31",
        allowed_labels=("2022", "2023", "2024"),
        forbidden_labels=("2025H2",),
    ),
)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _candidate_metrics(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metrics = candidate.get("oos_metrics") if isinstance(candidate.get("oos_metrics"), Mapping) else {}
    if not metrics and isinstance(candidate.get("oos"), Mapping):
        metrics = candidate.get("oos")
    return {
        "trade_count": _to_int(metrics.get("trade_count", candidate.get("trade_count"))),
        "expectancy_pct": _to_float(metrics.get("expectancy_pct", candidate.get("expectancy_pct"))),
        "profit_factor": _to_float(metrics.get("profit_factor", candidate.get("profit_factor"))),
        "max_drawdown_pct": _to_float(metrics.get("max_drawdown_pct", candidate.get("max_drawdown_pct"))),
        "win_rate": _to_float(metrics.get("win_rate", candidate.get("win_rate"))),
        "oos_member_score": _to_float(candidate.get("oos_member_score")),
        "rank_is": _to_int(candidate.get("rank_is"), 9999),
        "rulebook_hash": str(candidate.get("rulebook_hash") or ""),
    }


def _passes_label(candidate: Mapping[str, Any]) -> bool:
    metrics = _candidate_metrics(candidate)
    return (
        int(metrics["trade_count"]) >= MIN_TRADES
        and float(metrics["oos_member_score"]) >= MIN_MEMBER_SCORE
        and float(metrics["expectancy_pct"]) >= MIN_EXPECTANCY_PCT
    )


def _best_label_candidate(candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    passing = [dict(candidate) for candidate in candidates if _passes_label(candidate)]
    if not passing:
        return None
    return max(
        passing,
        key=lambda candidate: (
            _candidate_metrics(candidate)["expectancy_pct"],
            _candidate_metrics(candidate)["profit_factor"],
            _candidate_metrics(candidate)["oos_member_score"],
            -_candidate_metrics(candidate)["rank_is"],
        ),
    )


def _topn_by_ticker_label(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        label = str(row.get("label") or "")
        if not ticker or not label:
            continue
        out.setdefault(ticker, {})[label] = row
    return out


def _current_rulebook_metadata(ticker: str) -> dict[str, Any]:
    path = Path(PARAMS_PATH_TMPL.format(ticker=ticker))
    if not path.exists():
        return {"parameters_path": str(path), "parameters_exists": False}
    payload = json.loads(path.read_text(encoding="utf-8"))
    promotion = payload.get("promotion") if isinstance(payload.get("promotion"), Mapping) else {}
    rulebook = payload.get("rulebook") if isinstance(payload.get("rulebook"), Mapping) else {}
    return {
        "parameters_path": str(path),
        "parameters_exists": True,
        "current_promotion_id": promotion.get("promotion_id"),
        "current_selected_rulebook_hash": promotion.get("selected_rulebook_hash") or rulebook.get("selected_rulebook_hash") or rulebook.get("rulebook_hash"),
        "current_rulebook_signal_threshold": rulebook.get("signal_threshold"),
        "current_rulebook_expectancy_pct": rulebook.get("expectancy_pct"),
        "current_rulebook_fitness": rulebook.get("fitness"),
    }


def _build_selection_row(ticker: str, labels: dict[str, dict[str, Any]], spec: AsOfSpec) -> dict[str, Any] | None:
    label_rows: dict[str, dict[str, Any]] = {}
    for label in spec.allowed_labels:
        row = labels.get(label)
        if not row:
            return None
        candidate = _best_label_candidate(row.get("candidates") or [])
        if candidate is None:
            return None
        metrics = _candidate_metrics(candidate)
        label_rows[label] = {
            "label": label,
            "year": row.get("year"),
            "run_key": row.get("run_key"),
            "split": row.get("split"),
            "rulebook_hash": metrics["rulebook_hash"],
            "rank_is": metrics["rank_is"],
            "trade_count": metrics["trade_count"],
            "expectancy_pct": metrics["expectancy_pct"],
            "profit_factor": metrics["profit_factor"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "win_rate": metrics["win_rate"],
            "oos_member_score": metrics["oos_member_score"],
        }

    if len(label_rows) != len(spec.allowed_labels):
        return None

    exps = [float(row["expectancy_pct"]) for row in label_rows.values()]
    pfs = [float(row["profit_factor"]) for row in label_rows.values()]
    scores = [float(row["oos_member_score"]) for row in label_rows.values()]
    ranks = [int(row["rank_is"]) for row in label_rows.values()]
    trades = [int(row["trade_count"]) for row in label_rows.values()]
    drawdowns = [float(row["max_drawdown_pct"]) for row in label_rows.values()]
    current_meta = _current_rulebook_metadata(ticker)
    return {
        "ticker": ticker,
        "eligible_label_count": len(label_rows),
        "eligible_labels": list(spec.allowed_labels),
        "forbidden_labels": list(spec.forbidden_labels),
        "avg_expectancy_pct": mean(exps),
        "min_expectancy_pct": min(exps),
        "avg_profit_factor": mean(pfs),
        "avg_member_score": mean(scores),
        "worst_year_member_score": min(scores),
        "avg_rank_is": mean(ranks),
        "min_trades": min(trades),
        "avg_trades": mean(trades),
        "worst_drawdown_pct": min(drawdowns),
        "evidence_by_label": label_rows,
        **current_meta,
    }


def _sort_selection_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -int(row["eligible_label_count"]),
            -float(row["min_expectancy_pct"]),
            -float(row["avg_expectancy_pct"]),
            -float(row["avg_profit_factor"]),
            float(row["avg_rank_is"]),
            str(row["ticker"]),
        ),
    )


def build_pit_manifest(rows: list[dict[str, Any]], spec: AsOfSpec, *, top_n: int = PRIMARY_TOP_N) -> dict[str, Any]:
    by_ticker_label = _topn_by_ticker_label(rows)
    selected_rows: list[dict[str, Any]] = []
    rejected_missing_params: list[str] = []
    candidate_count = 0
    for ticker, labels in by_ticker_label.items():
        selection = _build_selection_row(ticker, labels, spec)
        if selection is None:
            continue
        candidate_count += 1
        if not selection.get("parameters_exists"):
            rejected_missing_params.append(ticker)
            continue
        selected_rows.append(selection)

    ranked = _sort_selection_rows(selected_rows)
    chosen = ranked[:top_n]
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "manifest_type": "pit_universe_bias_probe",
        "selection_rule_id": SELECTION_RULE_ID,
        "source_topn_path": str(TOPN_PATH),
        "as_of_date": spec.as_of_date,
        "trade_start_date": spec.trade_start_date,
        "trade_end_date": spec.trade_end_date,
        "allowed_labels": list(spec.allowed_labels),
        "forbidden_labels": list(spec.forbidden_labels),
        "required_pass_count": len(spec.allowed_labels),
        "top_n": top_n,
        "candidate_count_before_topn": candidate_count,
        "candidate_count_after_parameter_filter": len(selected_rows),
        "rejected_missing_parameters": sorted(rejected_missing_params),
        "count": len(chosen),
        "tickers": [row["ticker"] for row in chosen],
        "items": chosen,
        "created_at": created_at,
        "limitations": [
            "universe-only PIT probe; executable rulebooks are current data/symbols parameters",
            "2025H2 stress label is forbidden for 2024/2025 trading universe selection",
            "does not rerun GA or T+1/conservative_core training",
        ],
    }


def validate_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {str(label) for label in manifest.get("forbidden_labels", [])}
    allowed = {str(label) for label in manifest.get("allowed_labels", [])}
    violations: list[dict[str, Any]] = []
    missing_params: list[str] = []
    for item in manifest.get("items", []) or []:
        ticker = str(item.get("ticker") or "")
        if not item.get("parameters_exists"):
            missing_params.append(ticker)
        evidence = item.get("evidence_by_label") if isinstance(item.get("evidence_by_label"), Mapping) else {}
        labels = {str(label) for label in evidence.keys()}
        bad = sorted(labels & forbidden)
        not_allowed = sorted(labels - allowed)
        if bad or not_allowed:
            violations.append({"ticker": ticker, "forbidden_labels": bad, "not_allowed_labels": not_allowed})
        if labels != allowed:
            violations.append({"ticker": ticker, "missing_allowed_labels": sorted(allowed - labels)})
    return {
        "as_of_date": manifest.get("as_of_date"),
        "count": manifest.get("count"),
        "forbidden_label_violation_count": len(violations),
        "violations": violations[:20],
        "missing_parameters_count": len(missing_params),
        "missing_parameters": sorted(missing_params),
        "passed": not violations and not missing_params and int(manifest.get("count") or 0) > 0,
    }


def run_pit_universe_manifest_builder(
    *,
    topn_path: Path = TOPN_PATH,
    out_dir: Path = OUT_DIR,
    top_n: int = PRIMARY_TOP_N,
) -> dict[str, Any]:
    rows = _load_jsonl(topn_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifests: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    for spec in AS_OF_SPECS:
        manifest = build_pit_manifest(rows, spec, top_n=top_n)
        path = out_dir / f"universe_asof_{spec.as_of_date}.json"
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        validation = validate_manifest(manifest)
        validation["path"] = str(path)
        manifests.append(manifest)
        validations.append(validation)

    fixed16 = set("CAKE CRWD CW EME ETR HSBC ITT KT LASR MPC MPLX MTB NBIX WAB WELL WPM".split())
    overlap = {
        str(manifest["as_of_date"]): {
            "tickers": manifest["tickers"],
            "fixed16_overlap_count": len(set(manifest["tickers"]) & fixed16),
            "fixed16_overlap": sorted(set(manifest["tickers"]) & fixed16),
            "new_vs_fixed16": sorted(set(manifest["tickers"]) - fixed16),
            "missing_from_fixed16": sorted(fixed16 - set(manifest["tickers"])),
        }
        for manifest in manifests
    }
    summary = {
        "gate": "pit_universe_manifest_builder",
        "selection_rule_id": SELECTION_RULE_ID,
        "source_topn_path": str(topn_path),
        "out_dir": str(out_dir),
        "top_n": top_n,
        "as_of_count": len(AS_OF_SPECS),
        "validations": validations,
        "fixed16_overlap": overlap,
        "passed": all(v["passed"] for v in validations),
    }
    (out_dir / "manifest_builder_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
