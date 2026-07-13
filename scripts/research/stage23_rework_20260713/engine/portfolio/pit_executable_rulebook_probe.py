"""PIT executable-rulebook source probe for LR8D.

This probe sits between the universe-only PIT bias probe and a full PIT rerun.
It keeps the PIT universe manifests fixed, but replaces the current promoted
executable rulebook with an as-of-allowed top-N rulebook artifact from
lr8d_abcd_topn_rulebooks.jsonl.

It still does not rerun GA. Therefore it is not a full PIT baseline; it is a
low-cost probe for the remaining rulebook-source look-ahead premium.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

import pandas as pd

import engine.portfolio.noop_gate as ng
from engine.portfolio.pit_universe_bias_probe import (
    AS_OF_SPECS,
    FIXED16_BASELINE_CSV,
    OUT_DIR as PIT_UNIVERSE_OUT_DIR,
    SELECTION_RULE_ID,
    _bias_interpretation,
    _cap_binding_summary,
    _eligibility_for_decision_date,
    _load_csv_rows,
    _load_rulebooks_for_tickers,
    _manifest_map,
    _to_float,
    _to_int,
    _trade_metrics,
    _write_csv,
    run_pit_universe_manifest_builder,
)
from engine.strategies.evaluator import calc_position_size_krw, evaluate_signal
from engine.strategies.rulebook import Rulebook

RULEBOOKS_PATH = Path("data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn_rulebooks.jsonl")
OUT_DIR = Path("data/_system/research/central_portfolio/pit_executable_rulebook_probe")
PIT_UNIVERSE_ONLY_SUMMARY = PIT_UNIVERSE_OUT_DIR / "summary.json"
RULEBOOK_SELECTION_RULE_ID = "pit_allowed_label_best_expectancy_v0"
TIME_OUT_REASONS = {"time_out", "timeout"}


def _load_rulebook_artifacts(path: Path = RULEBOOKS_PATH) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            rulebook_hash = str(row.get("rulebook_hash") or "")
            rulebook = row.get("rulebook") if isinstance(row.get("rulebook"), dict) else None
            if rulebook_hash and rulebook:
                artifacts[rulebook_hash] = {
                    "rulebook": rulebook,
                    "ticker": row.get("ticker"),
                    "year": row.get("year"),
                    "run_key": row.get("run_key"),
                    "rank_is": row.get("rank_is"),
                }
    return artifacts


def _choose_executable_evidence(item: Mapping[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence_by_label") if isinstance(item.get("evidence_by_label"), Mapping) else {}
    candidates = [dict(value) for value in evidence.values() if isinstance(value, Mapping)]
    if not candidates:
        raise ValueError(f"no allowed-label evidence for ticker={item.get('ticker')}")
    chosen = max(
        candidates,
        key=lambda row: (
            _to_float(row.get("expectancy_pct")),
            _to_float(row.get("profit_factor")),
            _to_float(row.get("oos_member_score")),
            -_to_int(row.get("rank_is"), 9999),
        ),
    )
    if not chosen.get("rulebook_hash"):
        raise ValueError(f"missing rulebook_hash for ticker={item.get('ticker')} label={chosen.get('label')}")
    return chosen


def _build_rulebook_plan(
    manifests: list[dict[str, Any]],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], Rulebook], dict[str, Any]]:
    plan: dict[tuple[str, str], Rulebook] = {}
    selected: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    selected_label_counts: Counter[str] = Counter()
    for manifest in manifests:
        as_of = str(manifest.get("as_of_date") or "")
        forbidden = set(str(label) for label in manifest.get("forbidden_labels") or [])
        allowed = set(str(label) for label in manifest.get("allowed_labels") or [])
        for item in manifest.get("items") or []:
            ticker = str(item.get("ticker") or "")
            chosen = _choose_executable_evidence(item)
            label = str(chosen.get("label") or "")
            rulebook_hash = str(chosen.get("rulebook_hash") or "")
            if label in forbidden or label not in allowed:
                missing.append(
                    {
                        "ticker": ticker,
                        "as_of_date": as_of,
                        "rulebook_hash": rulebook_hash,
                        "label": label,
                        "reason": "label_not_allowed_or_forbidden",
                    }
                )
                continue
            artifact = artifacts.get(rulebook_hash)
            if not artifact:
                missing.append(
                    {
                        "ticker": ticker,
                        "as_of_date": as_of,
                        "rulebook_hash": rulebook_hash,
                        "label": label,
                        "reason": "missing_rulebook_artifact",
                    }
                )
                continue
            plan[(ticker, as_of)] = Rulebook.from_dict(dict(artifact["rulebook"]))
            selected_label_counts[label] += 1
            selected.append(
                {
                    "ticker": ticker,
                    "as_of_date": as_of,
                    "selected_label": label,
                    "rulebook_hash": rulebook_hash,
                    "expectancy_pct": _to_float(chosen.get("expectancy_pct")),
                    "profit_factor": _to_float(chosen.get("profit_factor")),
                    "oos_member_score": _to_float(chosen.get("oos_member_score")),
                    "rank_is": _to_int(chosen.get("rank_is"), 9999),
                    "artifact_year": artifact.get("year"),
                    "artifact_run_key": artifact.get("run_key"),
                }
            )
    return plan, {
        "rulebook_selection_rule_id": RULEBOOK_SELECTION_RULE_ID,
        "selected_count": len(selected),
        "missing_count": len(missing),
        "missing": missing[:50],
        "selected_label_counts": dict(sorted(selected_label_counts.items())),
        "selected": selected,
        "passed": len(missing) == 0 and len(selected) > 0,
    }


def _run_pit_dynamic_executable_loop(
    tickers: list[str],
    history_rulebooks: list[tuple[str, Rulebook]],
    histories: dict[str, Any],
    manifests: list[dict[str, Any]],
    rulebook_plan: Mapping[tuple[str, str], Rulebook],
    *,
    start_date: str,
    end_date: str,
    position_limit_krw: float,
    commission_rate: float,
    warmup: int,
    cooldown_days: int = 1,
) -> dict[str, list[dict[str, Any]]]:
    manifests_by_asof = _manifest_map(manifests)
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date) if end_date else None
    states: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        df = histories[ticker]
        states[ticker] = {
            "ticker": ticker,
            "df": df,
            "n": len(df),
            "date_series": ng._date_series_for_df(df),
            "next_idx": max(warmup, 0),
            "done": False,
            "trades": [],
        }

    # history_rulebooks is used only to define the global date axis in a stable way.
    axis_states: dict[str, dict[str, Any]] = {}
    for ticker, rb in history_rulebooks:
        df = histories[ticker]
        axis_states[ticker] = {
            "ticker": ticker,
            "rb": rb,
            "df": df,
            "n": len(df),
            "date_series": ng._date_series_for_df(df),
            "next_idx": max(warmup, 0),
            "done": False,
            "trades": [],
        }

    for current_ts in ng._global_date_axis(axis_states, warmup):
        for ticker in tickers:
            state = states[ticker]
            if state["done"]:
                continue
            df = state["df"]
            date_series = state["date_series"]

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

                eligible, as_of = _eligibility_for_decision_date(ticker, cur_ts, manifests_by_asof)
                if not eligible:
                    state["next_idx"] = idx + 1
                    continue
                rb = rulebook_plan.get((ticker, as_of))
                if rb is None:
                    state["next_idx"] = idx + 1
                    continue

                topic_window = ng._news_zscore_window(rb)
                topic_feature_map = ng._precompute_topic_feature_map(None, topic_window)
                sector_name = getattr(rb, "sector_name", "tech") or "tech"
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

                fill_idx = idx + 1
                if fill_idx >= state["n"]:
                    state["done"] = True
                    break
                fill_ts = ng._date_at(date_series, fill_idx) if date_series is not None else None
                if end_ts is not None and fill_ts is not None and fill_ts > end_ts:
                    state["done"] = True
                    break
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
                    fill_idx,
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
                    entry_idx=fill_idx,
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
                    topic_window=topic_window,
                    use_llm_events=True,
                )
                try:
                    trade["decision_date"] = str(pd.Timestamp(df.index[idx]).date())
                except Exception:
                    trade["decision_date"] = ""
                trade["pit_universe_as_of"] = as_of
                trade["pit_selection_rule_id"] = SELECTION_RULE_ID
                trade["pit_rulebook_selection_rule_id"] = RULEBOOK_SELECTION_RULE_ID
                state["trades"].append(trade)

                if exit_idx is None:
                    exit_date = trade.get("exit_date") if isinstance(trade, dict) else None
                    exit_idx = ng._find_df_index_by_date(df, exit_date)
                if exit_idx is None:
                    exit_idx = fill_idx + 1
                state["next_idx"] = max(exit_idx + 1 + cooldown_days, fill_idx + 1)

    return {ticker: list(state["trades"]) for ticker, state in states.items()}


def _metric_delta(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    pf_ref = _to_float(reference.get("profit_factor"))
    pf_cand = _to_float(candidate.get("profit_factor"))
    return {
        "trade_count_delta": _to_int(candidate.get("trade_count")) - _to_int(reference.get("trade_count")),
        "win_rate_delta_pp": _to_float(candidate.get("win_rate_pct")) - _to_float(reference.get("win_rate_pct")),
        "profit_factor_delta": pf_cand - pf_ref,
        "profit_factor_delta_pct": ((pf_cand / pf_ref - 1.0) * 100.0) if pf_ref > 0 else None,
        "return_delta_pp": _to_float(candidate.get("total_return_on_gross_entry_pct")) - _to_float(reference.get("total_return_on_gross_entry_pct")),
        "avg_trade_pnl_delta_pp": _to_float(candidate.get("avg_trade_pnl_pct")) - _to_float(reference.get("avg_trade_pnl_pct")),
    }


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_pit_executable_rulebook_probe(
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
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_summary = run_pit_universe_manifest_builder(out_dir=out_dir)
    manifests = [json.loads((out_dir / f"universe_asof_{spec.as_of_date}.json").read_text(encoding="utf-8")) for spec in AS_OF_SPECS]
    artifacts = _load_rulebook_artifacts(RULEBOOKS_PATH)
    rulebook_plan, rulebook_plan_summary = _build_rulebook_plan(manifests, artifacts)
    all_tickers = sorted({ticker for manifest in manifests for ticker in manifest.get("tickers", [])})
    history_rulebooks = _load_rulebooks_for_tickers(all_tickers)
    histories = ng.load_fixed_histories(history_rulebooks, years=years, history_end_date=history_end_date)
    trades_by_ticker = _run_pit_dynamic_executable_loop(
        all_tickers,
        history_rulebooks,
        histories,
        manifests,
        rulebook_plan,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
    )
    rows = ng.normalize_trade_map(history_rulebooks, trades_by_ticker)
    fixed_rows = _load_csv_rows(FIXED16_BASELINE_CSV)
    fixed_metrics = _trade_metrics(fixed_rows)
    candidate_metrics = _trade_metrics(rows)
    fixed_delta = _metric_delta(fixed_metrics, candidate_metrics)
    bias_vs_fixed = _bias_interpretation(fixed_metrics, candidate_metrics)
    universe_only_summary = _load_optional_json(PIT_UNIVERSE_ONLY_SUMMARY)
    universe_only_delta = None
    if universe_only_summary and isinstance(universe_only_summary.get("pit_metrics"), Mapping):
        universe_only_delta = _metric_delta(universe_only_summary["pit_metrics"], candidate_metrics)

    _write_csv(out_dir / "fixed16_reference_trades.csv", fixed_rows)
    _write_csv(out_dir / "candidate_trades.csv", rows)
    (out_dir / "rulebook_plan.json").write_text(json.dumps(rulebook_plan_summary, indent=2, default=str), encoding="utf-8")
    comparison = {
        "fixed16_metrics": fixed_metrics,
        "pit_executable_metrics": candidate_metrics,
        "delta_vs_fixed16": fixed_delta,
        "bias_vs_fixed16": bias_vs_fixed,
        "pit_universe_only_summary_path": str(PIT_UNIVERSE_ONLY_SUMMARY),
        "delta_vs_pit_universe_only": universe_only_delta,
        "fixed16_cap_binding_summary": _cap_binding_summary(fixed_rows),
        "pit_executable_cap_binding_summary": _cap_binding_summary(rows),
    }
    (out_dir / "comparison.json").write_text(json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    summary = {
        "gate": "pit_executable_rulebook_probe",
        "selection_rule_id": SELECTION_RULE_ID,
        "rulebook_selection_rule_id": RULEBOOK_SELECTION_RULE_ID,
        "source_rulebooks_path": str(RULEBOOKS_PATH),
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "entry_execution_mode": "t_plus_1_open",
        "exit_execution_mode": "conservative_core",
        "probe_limitation": "uses existing topn rulebook artifacts; does not rerun GA or train rulebooks under T+1/conservative_core",
        "manifest_summary": manifest_summary,
        "rulebook_plan_summary": {
            key: value for key, value in rulebook_plan_summary.items() if key != "selected"
        },
        "fixed16_metrics": fixed_metrics,
        "pit_executable_metrics": candidate_metrics,
        "delta_vs_fixed16": fixed_delta,
        "bias_vs_fixed16": bias_vs_fixed,
        "delta_vs_pit_universe_only": universe_only_delta,
        "fixed16_ref_trade_count": len(fixed_rows),
        "candidate_trade_count": len(rows),
        "candidate_active_ticker_count": len({row.get("ticker") for row in rows}),
        "candidate_trade_tickers": sorted(ticker for ticker in {row.get("ticker") for row in rows} if ticker),
        "cap_binding_summary": comparison["pit_executable_cap_binding_summary"],
        "passed": bool(manifest_summary.get("passed")) and bool(rulebook_plan_summary.get("passed")) and len(rows) > 0,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
