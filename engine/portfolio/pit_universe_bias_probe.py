"""Point-in-time universe manifest builder and bias probe for LR8D.

The v0 probe intentionally does not rerun GA or relearn rulebooks. It uses the
existing LR8D top-N OOS output to build as-of universe manifests, then measures
how much the current fixed-16 realistic baseline changes when new entries are
restricted to those point-in-time universes.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

import pandas as pd

import engine.portfolio.noop_gate as ng
from engine.strategies.evaluator import calc_position_size_krw, evaluate_signal
from engine.strategies.rulebook import Rulebook

TOPN_PATH = Path("data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn.jsonl")
OUT_DIR = Path("data/_system/research/central_portfolio/pit_universe_bias_probe")
FIXED16_BASELINE_CSV = Path("data/_system/research/central_portfolio/conservative_core_exit/candidate_trades.csv")
PARAMS_PATH_TMPL = "data/symbols/{ticker}/parameters.json"
SELECTION_RULE_ID = "pit_all_available_labels_top16_v0"
MIN_TRADES = 5
MIN_MEMBER_SCORE = 10.0
MIN_EXPECTANCY_PCT = 1.0
PRIMARY_TOP_N = 16
TIME_OUT_REASONS = {"time_out", "timeout"}


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


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _gross_entry(row: Mapping[str, Any]) -> float:
    shares = _to_float(row.get("total_shares"), _to_float(row.get("entry_shares")))
    return _to_float(row.get("entry_price")) * shares


def _trade_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gross = sum(_gross_entry(row) for row in rows)
    pnl = sum(_to_float(row.get("pnl_krw")) for row in rows)
    wins = [row for row in rows if _to_float(row.get("pnl_krw")) > 0]
    losses = [row for row in rows if _to_float(row.get("pnl_krw")) < 0]
    gross_profit = sum(_to_float(row.get("pnl_krw")) for row in wins)
    gross_loss = abs(sum(_to_float(row.get("pnl_krw")) for row in losses))
    holdings = [_to_int(row.get("holding_days")) for row in rows]
    reason_counts = Counter(str(row.get("exit_reason") or "") for row in rows)
    reason_pnl: dict[str, float] = defaultdict(float)
    reason_gross: dict[str, float] = defaultdict(float)
    ticker_counts = Counter(str(row.get("ticker") or "") for row in rows)
    ticker_pnl: dict[str, float] = defaultdict(float)
    ticker_gross: dict[str, float] = defaultdict(float)
    time_out_loss_count = 0
    time_out_loss_pnl = 0.0
    for row in rows:
        reason = str(row.get("exit_reason") or "")
        ticker = str(row.get("ticker") or "")
        row_pnl = _to_float(row.get("pnl_krw"))
        row_gross = _gross_entry(row)
        reason_pnl[reason] += row_pnl
        reason_gross[reason] += row_gross
        ticker_pnl[ticker] += row_pnl
        ticker_gross[ticker] += row_gross
        if reason in TIME_OUT_REASONS and row_pnl < 0:
            time_out_loss_count += 1
            time_out_loss_pnl += row_pnl
    worst_5 = sorted(rows, key=lambda row: _to_float(row.get("pnl_pct")))[:5]
    return {
        "trade_count": len(rows),
        "ticker_count": len(ticker_counts),
        "gross_entry_krw": gross,
        "total_pnl_krw": pnl,
        "total_return_on_gross_entry_pct": (pnl / gross * 100.0) if gross > 0 else 0.0,
        "avg_trade_pnl_pct": mean([_to_float(row.get("pnl_pct")) for row in rows]) if rows else 0.0,
        "win_rate_pct": (len(wins) / len(rows) * 100.0) if rows else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "avg_holding_days": mean(holdings) if holdings else 0.0,
        "max_holding_days": max(holdings) if holdings else 0,
        "exit_reason_counts": dict(sorted(reason_counts.items())),
        "exit_reason_pnl_krw": {key: float(value) for key, value in sorted(reason_pnl.items())},
        "exit_reason_gross_krw": {key: float(value) for key, value in sorted(reason_gross.items())},
        "time_out_total_pnl_krw": sum(value for reason, value in reason_pnl.items() if reason in TIME_OUT_REASONS),
        "time_out_loss_count": time_out_loss_count,
        "time_out_loss_pnl_krw": time_out_loss_pnl,
        "stop_loss_count": int(reason_counts.get("stop_loss", 0)),
        "ticker_trade_counts": dict(sorted(ticker_counts.items())),
        "ticker_pnl_krw": {key: float(value) for key, value in sorted(ticker_pnl.items())},
        "ticker_gross_share_pct": {key: (float(value) / gross * 100.0 if gross > 0 else 0.0) for key, value in sorted(ticker_gross.items())},
        "worst_5_trades": [
            {
                "ticker": row.get("ticker"),
                "entry_date": row.get("entry_date"),
                "exit_date": row.get("exit_date"),
                "exit_reason": row.get("exit_reason"),
                "pnl_pct": _to_float(row.get("pnl_pct")),
                "pnl_krw": _to_float(row.get("pnl_krw")),
            }
            for row in worst_5
        ],
    }


def _cap_binding_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dates = sorted({str(row.get("entry_date") or "") for row in rows} | {str(row.get("exit_date") or "") for row in rows})
    dates = [date for date in dates if date]
    counts: list[int] = []
    notionals: list[float] = []
    for date in dates:
        open_rows = [row for row in rows if str(row.get("entry_date") or "") <= date <= str(row.get("exit_date") or "")]
        counts.append(len(open_rows))
        notionals.append(sum(_gross_entry(row) for row in open_rows))
    return {
        "event_date_count": len(dates),
        "avg_open_positions_on_event_dates": mean(counts) if counts else 0.0,
        "max_open_positions_on_event_dates": max(counts) if counts else 0,
        "max_gross_exposure_on_event_dates": max(notionals) if notionals else 0.0,
        "binding_or_over_event_days_by_cap": {
            str(cap): sum(1 for value in notionals if value >= cap) for cap in (120, 180, 240, 300, 480, 600)
        },
    }


def _bias_interpretation(fixed_metrics: Mapping[str, Any], pit_metrics: Mapping[str, Any]) -> dict[str, Any]:
    win_delta = _to_float(pit_metrics.get("win_rate_pct")) - _to_float(fixed_metrics.get("win_rate_pct"))
    pf_fixed = _to_float(fixed_metrics.get("profit_factor"))
    pf_pit = _to_float(pit_metrics.get("profit_factor"))
    pf_delta_pct = ((pf_pit / pf_fixed - 1.0) * 100.0) if pf_fixed > 0 else 0.0
    ret_delta = _to_float(pit_metrics.get("total_return_on_gross_entry_pct")) - _to_float(fixed_metrics.get("total_return_on_gross_entry_pct"))
    severe = (
        _to_float(pit_metrics.get("win_rate_pct")) <= 60.0
        or pf_pit <= 2.0
        or _to_float(pit_metrics.get("total_return_on_gross_entry_pct")) <= 0.0
    )
    material = win_delta < -5.0 or pf_delta_pct < -20.0 or ret_delta < -1.0
    if severe:
        label = "bias_severe_or_baseline_not_live_reliable"
    elif material:
        label = "bias_material"
    else:
        label = "bias_small_by_predefined_thresholds"
    return {
        "label": label,
        "win_rate_delta_pp": win_delta,
        "profit_factor_delta_pct": pf_delta_pct,
        "return_delta_pp": ret_delta,
        "thresholds": {
            "bias_small": "win_rate drop <=5pp, PF drop <=20%, return drop <=1.0pp",
            "bias_material": "any small threshold exceeded",
            "bias_severe": "PIT win_rate <=60%, PF <=2.0, or return <=0",
        },
    }


def _load_rulebooks_for_tickers(tickers: Iterable[str]) -> list[tuple[str, Rulebook]]:
    out: list[tuple[str, Rulebook]] = []
    for ticker in sorted(set(tickers)):
        payload = json.loads(Path(PARAMS_PATH_TMPL.format(ticker=ticker)).read_text(encoding="utf-8"))
        out.append((ticker, Rulebook.from_dict(payload["rulebook"])))
    return out


def _manifest_map(manifests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(manifest["as_of_date"]): manifest for manifest in manifests}


def _eligibility_for_decision_date(ticker: str, decision_ts: pd.Timestamp, manifests_by_asof: Mapping[str, Mapping[str, Any]]) -> tuple[bool, str]:
    decision_date = str(pd.Timestamp(decision_ts).date())
    if "2024-01-01" <= decision_date <= "2024-12-31":
        as_of = "2023-12-31"
    elif "2025-01-01" <= decision_date <= "2025-12-31":
        as_of = "2024-12-31"
    else:
        return False, ""
    manifest = manifests_by_asof.get(as_of) or {}
    return ticker in set(manifest.get("tickers") or []), as_of


def _run_pit_dynamic_universe_loop(
    rulebooks: list[tuple[str, Rulebook]],
    histories: dict[str, Any],
    manifests: list[dict[str, Any]],
    *,
    start_date: str,
    end_date: str,
    position_limit_krw: float,
    commission_rate: float,
    warmup: int,
    years: int,
    cooldown_days: int = 1,
) -> dict[str, list[dict[str, Any]]]:
    manifests_by_asof = _manifest_map(manifests)
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date) if end_date else None
    states: dict[str, dict[str, Any]] = {}
    for ticker, rb in rulebooks:
        df = histories[ticker]
        topic_window = ng._news_zscore_window(rb)
        states[ticker] = {
            "ticker": ticker,
            "rb": rb,
            "df": df,
            "n": len(df),
            "date_series": ng._date_series_for_df(df),
            "next_idx": max(warmup, 0),
            "done": False,
            "trades": [],
            "topic_window": topic_window,
            "topic_feature_map": ng._precompute_topic_feature_map(None, topic_window),
            "sector_name": getattr(rb, "sector_name", "tech") or "tech",
        }

    for current_ts in ng._global_date_axis(states, warmup):
        for ticker, _ in rulebooks:
            state = states[ticker]
            if state["done"]:
                continue
            rb = state["rb"]
            df = state["df"]
            date_series = state["date_series"]
            topic_feature_map = state["topic_feature_map"]
            sector_name = state["sector_name"]

            while state["next_idx"] < state["n"]:
                idx = int(state["next_idx"])
                if date_series is not None:
                    try:
                        cur_ts = ng._date_at(date_series, idx)
                        if cur_ts is not None and cur_ts > current_ts:
                            break
                        if start_ts is not None and cur_ts is not None and cur_ts < start_ts:
                            state["next_idx"] = idx + 1
                            continue
                        if end_ts is not None and cur_ts is not None and cur_ts > end_ts:
                            state["done"] = True
                            break
                    except Exception:
                        cur_ts = current_ts
                else:
                    cur_ts = current_ts

                sub_df = df.iloc[: idx + 1]
                cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = ng._lookup_signal_context(
                    df=df,
                    idx=idx,
                    market_score=50.0,
                    sector_score=50.0,
                    vix_level=18.0,
                    market_history_df=None,
                    sector_name=sector_name,
                    ticker_sentiment=None,
                    topic_feature_map=topic_feature_map,
                    use_llm_events=True,
                )
                sig = evaluate_signal(
                    rb,
                    sub_df,
                    market_score=cur_market,
                    sector_score=cur_sector,
                    vix_level=cur_vix,
                    news_sentiment=cur_sentiment,
                    event_flags=cur_event_flags,
                    topic_features=cur_topic_features,
                )
                if not sig.should_buy:
                    state["next_idx"] = idx + 1
                    continue

                eligible, as_of = _eligibility_for_decision_date(ticker, cur_ts, manifests_by_asof)
                if not eligible:
                    state["next_idx"] = idx + 1
                    continue

                entry_exec_idx = idx
                fill_idx = idx + 1
                if fill_idx >= state["n"]:
                    state["done"] = True
                    break
                fill_ts = ng._date_at(date_series, fill_idx) if date_series is not None else None
                if end_ts is not None and fill_ts is not None and fill_ts > end_ts:
                    state["done"] = True
                    break
                entry_exec_idx = fill_idx
                entry_price = float(df.iloc[fill_idx]["Open"])
                try:
                    entry_atr_override = float(df.iloc[idx].get("ATR", entry_price * 0.02))
                except Exception:
                    entry_atr_override = entry_price * 0.02

                amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
                shares = amt_krw / entry_price if entry_price > 0 else 0.0
                if shares <= 0:
                    state["next_idx"] = idx + 1
                    continue

                trade_obj = ng.simulate_exit(
                    rb,
                    df,
                    entry_exec_idx,
                    shares,
                    position_limit_krw,
                    commission_rate=commission_rate,
                    cur_market_score=cur_market,
                    cur_vix_level=cur_vix,
                    cur_sector_score=cur_sector,
                    fractional_shares=True,
                    disable_add_buy=True,
                    live_hard_stop_guard=False,
                    entry_price_override=entry_price,
                    entry_atr_override=entry_atr_override,
                    exit_execution_mode="conservative_core",
                )
                if trade_obj is None:
                    state["done"] = True
                    break

                trade, exit_idx = ng._attach_legacy_trade_metadata(
                    rb=rb,
                    df=df,
                    entry_idx=entry_exec_idx,
                    trade_obj=trade_obj,
                    sig=sig,
                    cur_sentiment=cur_sentiment,
                    cur_market=cur_market,
                    cur_sector=cur_sector,
                    cur_vix=cur_vix,
                    cur_event_flags=cur_event_flags,
                    cur_topic_features=cur_topic_features,
                    market_score=50.0,
                    sector_score=50.0,
                    vix_level=18.0,
                    market_history_df=None,
                    sector_name=sector_name,
                    ticker_sentiment=None,
                    topic_feature_map=topic_feature_map,
                    position_limit_krw=position_limit_krw,
                    commission_rate=commission_rate,
                    cooldown_days=cooldown_days,
                    warmup=warmup,
                    start_date=start_date,
                    end_date=end_date,
                    topic_window=state["topic_window"],
                    use_llm_events=True,
                )
                try:
                    trade["decision_date"] = str(pd.Timestamp(df.index[idx]).date())
                except Exception:
                    trade["decision_date"] = ""
                trade["pit_universe_as_of"] = as_of
                trade["pit_selection_rule_id"] = SELECTION_RULE_ID
                state["trades"].append(trade)

                if exit_idx is None:
                    exit_date = trade.get("exit_date") if isinstance(trade, dict) else None
                    exit_idx = ng._find_df_index_by_date(df, exit_date)
                if exit_idx is None:
                    exit_idx = entry_exec_idx + 1
                state["next_idx"] = max(exit_idx + 1 + cooldown_days, entry_exec_idx + 1)

    return {ticker: list(state["trades"]) for ticker, state in states.items()}


def run_pit_universe_bias_probe(
    *,
    start_date: str = "2024-01-01",
    end_date: str = "2025-12-31",
    history_end_date: str = "2026-06-09",
    position_limit_krw: float = 30.0,
    commission_rate: float = 0.0005,
    warmup: int = 200,
    years: int = 3,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    manifest_summary = run_pit_universe_manifest_builder(out_dir=out_dir)
    manifests = [json.loads((out_dir / f"universe_asof_{spec.as_of_date}.json").read_text(encoding="utf-8")) for spec in AS_OF_SPECS]
    all_tickers = sorted({ticker for manifest in manifests for ticker in manifest.get("tickers", [])})
    rulebooks = _load_rulebooks_for_tickers(all_tickers)
    histories = ng.load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)
    pit_trades_by_ticker = _run_pit_dynamic_universe_loop(
        rulebooks,
        histories,
        manifests,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
        years=years,
    )
    pit_rows = ng.normalize_trade_map(rulebooks, pit_trades_by_ticker)
    fixed_rows = _load_csv_rows(FIXED16_BASELINE_CSV)
    fixed_metrics = _trade_metrics(fixed_rows)
    pit_metrics = _trade_metrics(pit_rows)
    bias = _bias_interpretation(fixed_metrics, pit_metrics)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "fixed16_reference_trades.csv", fixed_rows)
    _write_csv(out_dir / "candidate_trades.csv", pit_rows)
    fixed_tickers = {row.get("ticker") for row in fixed_rows}
    pit_tickers = {row.get("ticker") for row in pit_rows}
    comparison = {
        "fixed16_metrics": fixed_metrics,
        "pit_metrics": pit_metrics,
        "survivorship_bias_delta": bias,
        "fixed16_trade_tickers": sorted(ticker for ticker in fixed_tickers if ticker),
        "pit_trade_tickers": sorted(ticker for ticker in pit_tickers if ticker),
        "pit_new_trade_tickers_vs_fixed16": sorted(ticker for ticker in pit_tickers - fixed_tickers if ticker),
        "fixed16_missing_trade_tickers_in_pit": sorted(ticker for ticker in fixed_tickers - pit_tickers if ticker),
        "fixed16_cap_binding_summary": _cap_binding_summary(fixed_rows),
        "pit_cap_binding_summary": _cap_binding_summary(pit_rows),
    }
    (out_dir / "comparison_vs_fixed16.json").write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    summary = {
        "gate": "pit_universe_bias_probe",
        "selection_rule_id": SELECTION_RULE_ID,
        "source_topn_path": str(TOPN_PATH),
        "fixed16_baseline_csv": str(FIXED16_BASELINE_CSV),
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "entry_execution_mode": "t_plus_1_open",
        "exit_execution_mode": "conservative_core",
        "rulebook_limitation": "universe-only PIT probe; executable rulebooks are current data/symbols parameters",
        "manifest_summary": manifest_summary,
        "as_of_manifests": [
            {
                "as_of_date": manifest.get("as_of_date"),
                "trade_start_date": manifest.get("trade_start_date"),
                "trade_end_date": manifest.get("trade_end_date"),
                "tickers": manifest.get("tickers"),
                "count": manifest.get("count"),
            }
            for manifest in manifests
        ],
        "fixed16_metrics": fixed_metrics,
        "pit_metrics": pit_metrics,
        "survivorship_bias_delta": bias,
        "fixed16_ref_trade_count": len(fixed_rows),
        "candidate_trade_count": len(pit_rows),
        "fixed16_active_ticker_count": len(fixed_tickers),
        "pit_active_ticker_count": len(pit_tickers),
        "fixed16_trade_tickers": sorted(ticker for ticker in fixed_tickers if ticker),
        "pit_trade_tickers": sorted(ticker for ticker in pit_tickers if ticker),
        "cap_binding_summary": comparison["pit_cap_binding_summary"],
        "passed": bool(manifest_summary.get("passed")) and len(pit_rows) > 0,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
