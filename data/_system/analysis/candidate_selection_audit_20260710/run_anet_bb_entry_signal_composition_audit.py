from __future__ import annotations

"""ANET·BB 진입/신호 시점 구성 read-only 감사.

원본·라이브·운영 코드·설정·주문을 변경하지 않고 기존 로그·상태·분석 snapshot만 읽는다.
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
IDS = {
    "ANET": "stage3:ANET:fe220620802b",
    "BB": "stage3:BB:f1bdfe7f8ad9",
}
KST = ZoneInfo("Asia/Seoul")

SOURCE_OUT = OUT / "anet_bb_signal_source_coverage.csv"
EVENT_OUT = OUT / "anet_bb_entry_signal_events.csv"
SNAPSHOT_OUT = OUT / "anet_bb_point_signal_components.csv"
COMPARE_OUT = OUT / "anet_bb_signal_comparison.csv"
SUMMARY_OUT = OUT / "anet_bb_entry_signal_summary.json"
READOUT_OUT = OUT / "anet_bb_entry_signal_readout.md"


def stable_json(path: Path) -> Any:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    value = json.loads(path.read_text(encoding="utf-8"))
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return value


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def jsonl(path: Path) -> list[dict[str, Any]]:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except Exception:
                continue
            if isinstance(value, dict):
                rows.append(value)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return rows


def to_kst(value: Any) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(KST).isoformat()
    except Exception:
        return None


def numeric(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def source_row(
    source: str,
    path: str,
    ticker: str,
    record_n: int,
    timing: str,
    score: bool,
    raw_score: bool,
    threshold: bool,
    ratio: bool,
    components: bool,
    market_context: bool,
    news_event: bool,
    outcome: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "path": path,
        "ticker": ticker,
        "candidate_id": IDS[ticker],
        "matching_record_n": record_n,
        "timing_class": timing,
        "has_score": score,
        "has_raw_score": raw_score,
        "has_threshold": threshold,
        "has_ratio": ratio,
        "has_component_breakdown": components,
        "has_market_context": market_context,
        "has_news_event_fields": news_event,
        "has_trade_outcome": outcome,
        "entry_time_component_verifiable": timing == "HISTORICAL_ENTRY" and components,
        "note": note,
    }


def main() -> int:
    shadow_path = ROOT / "data/_system/elite_shadow_trades.jsonl"
    strategy_path = ROOT / "data/_system/elite_strategy_sim_trades.jsonl"
    slots_events_path = ROOT / "data/_system/live_slots_events.jsonl"
    auto_events_path = ROOT / "data/_system/live_auto_events.jsonl"
    replay_path = ROOT / "data/_system/research/central_portfolio/daily_signal_replay/daily_signal_replay.jsonl"

    shadow_all = jsonl(shadow_path)
    strategy_all = jsonl(strategy_path)
    slot_events_all = jsonl(slots_events_path)
    auto_events_all = jsonl(auto_events_path)
    replay_all = jsonl(replay_path)

    shadow = [row for row in shadow_all if row.get("candidate_id") in IDS.values()]
    strategy = [row for row in strategy_all if row.get("candidate_id") in IDS.values()]
    slot_events = [row for row in slot_events_all if row.get("candidate_id") in IDS.values()]
    auto_events = [row for row in auto_events_all if row.get("candidate_id") in IDS.values()]
    replay = [row for row in replay_all if row.get("candidate_id") in IDS.values()]

    live93 = stable_csv(OUT / "live93_three_symptom_scan.csv", low_memory=False)
    live93 = live93[live93["candidate_id"].isin(IDS.values())].copy()
    coverage_prior = stable_csv(OUT / "ce_dynamic_ce7_snapshot_coverage.csv", low_memory=False)
    coverage_prior = coverage_prior[coverage_prior["candidate_id"].isin(IDS.values())].copy()
    canonical = coverage_prior.set_index("candidate_id")

    dashboard_json = stable_json(ROOT / "data/_system/real_dashboard_buy_candidates.json")
    dashboard = dashboard_json.get("candidates") or {}
    dashboard_updated_at = dashboard_json.get("updated_at")

    slots_state = stable_json(ROOT / "data/_system/live_slots_state.json")
    slot_pool = {
        str(row.get("candidate_id")): row
        for row in (slots_state.get("candidate_pool") or [])
        if row.get("candidate_id") in IDS.values()
    }

    pending = stable_json(ROOT / "data/_system/pending_orders.json")
    pending_rows: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in IDS}
    for row in (pending.get("orders") or {}).values():
        candidate_id = str((row.get("metadata") or {}).get("candidate_id") or "")
        for ticker, cid in IDS.items():
            if candidate_id == cid:
                pending_rows[ticker].append(row)

    shadow_state = stable_json(ROOT / "data/_system/elite_shadow_state.json")
    shadow_events = [
        row for row in (shadow_state.get("events") or [])
        if row.get("candidate_id") in IDS.values()
    ]

    event_rows: list[dict[str, Any]] = []
    for row in shadow:
        score = numeric(row.get("entry_score"))
        threshold = numeric(row.get("entry_threshold"))
        ratio = numeric(row.get("entry_ratio"))
        if ratio is None and score is not None and threshold and threshold > 0:
            ratio = score / threshold
        event_rows.append({
            "record_type": "SHADOW_CLOSED_TRADE_ENTRY",
            "source": "elite_shadow_trades.jsonl",
            "candidate_id": row.get("candidate_id"),
            "ticker": row.get("ticker"),
            "position_id": row.get("position_id"),
            "signal_time_utc": row.get("opened_at"),
            "signal_time_kst": to_kst(row.get("opened_at")),
            "entry_price": numeric(row.get("entry_price")),
            "score": score,
            "raw_score": None,
            "threshold": threshold,
            "ratio": ratio,
            "score_minus_threshold": score - threshold if score is not None and threshold is not None else None,
            "ratio_lt_1_25": ratio < 1.25 if ratio is not None else None,
            "component_breakdown_available": False,
            "positive_component_n": None,
            "top2_share_pct": None,
            "technical_core_only": None,
            "news_event_bonus": None,
            "pnl_pct": numeric(row.get("pnl_pct")),
            "data_quality": "RATIO_AND_OUTCOME_ONLY_NO_COMPONENTS",
            "ce_symptom_verdict": "LOW_RATIO_ONLY" if ratio is not None and ratio < 1.25 else "NOT_LOW_RATIO_OR_UNKNOWN",
        })

    for row in strategy:
        score = numeric(row.get("entry_score"))
        threshold = numeric(row.get("entry_threshold"))
        ratio = numeric(row.get("entry_ratio"))
        event_rows.append({
            "record_type": "STRATEGY_SIM_ENTRY",
            "source": "elite_strategy_sim_trades.jsonl",
            "candidate_id": row.get("candidate_id"),
            "ticker": row.get("ticker"),
            "position_id": row.get("position_id"),
            "signal_time_utc": row.get("opened_at"),
            "signal_time_kst": to_kst(row.get("opened_at")),
            "entry_price": numeric(row.get("entry_price")),
            "score": score,
            "raw_score": None,
            "threshold": threshold,
            "ratio": ratio,
            "score_minus_threshold": score - threshold if score is not None and threshold is not None else None,
            "ratio_lt_1_25": ratio < 1.25 if ratio is not None else None,
            "component_breakdown_available": False,
            "positive_component_n": None,
            "top2_share_pct": None,
            "technical_core_only": None,
            "news_event_bonus": None,
            "pnl_pct": numeric(row.get("pnl_pct")),
            "data_quality": "RATIO_AND_OUTCOME_ONLY_NO_COMPONENTS",
            "ce_symptom_verdict": "LOW_RATIO_ONLY" if ratio is not None and ratio < 1.25 else "NOT_LOW_RATIO_OR_UNKNOWN",
        })

    for row in slot_events:
        if row.get("event") != "DASHBOARD_REAL_BUY_INTENT":
            continue
        event_rows.append({
            "record_type": "LIVE_ORDER_INTENT",
            "source": "live_slots_events.jsonl",
            "candidate_id": row.get("candidate_id"),
            "ticker": row.get("ticker"),
            "position_id": None,
            "signal_time_utc": row.get("time"),
            "signal_time_kst": to_kst(row.get("time")),
            "entry_price": None,
            "score": None,
            "raw_score": None,
            "threshold": None,
            "ratio": None,
            "score_minus_threshold": None,
            "ratio_lt_1_25": None,
            "component_breakdown_available": False,
            "positive_component_n": None,
            "top2_share_pct": None,
            "technical_core_only": None,
            "news_event_bonus": None,
            "pnl_pct": None,
            "data_quality": "INTENT_STATUS_ONLY_NO_SIGNAL_FIELDS",
            "ce_symptom_verdict": "UNVERIFIABLE",
            "intent_status": row.get("status"),
            "execution_mode": row.get("execution_mode"),
            "notional": numeric(row.get("notional")),
        })

    events_df = pd.DataFrame(event_rows).sort_values(["ticker", "signal_time_utc", "record_type"])
    events_df.to_csv(EVENT_OUT, index=False)

    snapshot_rows: list[dict[str, Any]] = []
    for ticker, cid in IDS.items():
        row = dashboard.get(cid) or {}
        components = row.get("components") if isinstance(row.get("components"), dict) else {}
        positive = sorted(
            [(str(name), float(value)) for name, value in components.items() if numeric(value) is not None and float(value) > 0],
            key=lambda item: item[1], reverse=True,
        )
        positive_sum = sum(value for _, value in positive)
        top2_sum = sum(value for _, value in positive[:2])
        score = numeric(row.get("final_score"))
        raw_score = numeric(row.get("raw_score"))
        threshold = numeric(row.get("threshold"))
        ratio = numeric(row.get("ratio"))
        if ratio is None and score is not None and threshold and threshold > 0:
            ratio = score / threshold
        bonus_sum = sum(float(components.get(name) or 0.0) for name in ("news", "news_topics", "events"))
        core_sum = sum(float(components.get(name) or 0.0) for name in ("ma_align", "macd", "rsi", "bb", "volume"))
        snapshot_rows.append({
            "snapshot_class": "POINT_IN_TIME_SIGNAL_NOT_ENTRY_TIME",
            "snapshot_source": "real_dashboard_buy_candidates.json",
            "snapshot_time_utc": dashboard_updated_at,
            "snapshot_time_kst": to_kst(dashboard_updated_at),
            "candidate_id": cid,
            "ticker": ticker,
            "score": score,
            "raw_score": raw_score,
            "threshold": threshold,
            "ratio": ratio,
            "score_minus_threshold": score - threshold if score is not None and threshold is not None else None,
            "threshold_excess_pct": (ratio - 1.0) * 100.0 if ratio is not None else None,
            "ratio_lt_1_25": ratio < 1.25 if ratio is not None else None,
            "component_ma_align": numeric(components.get("ma_align")) or 0.0,
            "component_macd": numeric(components.get("macd")) or 0.0,
            "component_rsi": numeric(components.get("rsi")) or 0.0,
            "component_bb": numeric(components.get("bb")) or 0.0,
            "component_volume": numeric(components.get("volume")) or 0.0,
            "component_news": numeric(components.get("news")) or 0.0,
            "component_news_topics": numeric(components.get("news_topics")) or 0.0,
            "component_events": numeric(components.get("events")) or 0.0,
            "positive_component_n": len(positive),
            "positive_component_sum": positive_sum,
            "top1_component": positive[0][0] if positive else None,
            "top1_share_pct": positive[0][1] / positive_sum * 100.0 if positive_sum > 0 else None,
            "top2_components": "+".join(name for name, _ in positive[:2]),
            "top2_share_pct": top2_sum / positive_sum * 100.0 if positive_sum > 0 else None,
            "technical_core_sum": core_sum,
            "news_event_bonus_sum": bonus_sum,
            "technical_core_only": bool(core_sum > 0 and bonus_sum == 0),
            "ce_low_ratio": bool(ratio is not None and ratio < 1.25),
            "ce_top2_concentrated": bool(positive_sum > 0 and top2_sum / positive_sum >= 0.90),
            "ce_point_snapshot_verdict": (
                "CE_LIKE_LOW_RATIO_AND_TOP2_CONCENTRATED"
                if ratio is not None and ratio < 1.25 and positive_sum > 0 and top2_sum / positive_sum >= 0.90
                else "NOT_FULL_CE_LIKE"
            ),
            "entry_time_equivalent": False,
        })

    snapshots_df = pd.DataFrame(snapshot_rows)
    snapshots_df.to_csv(SNAPSHOT_OUT, index=False)

    source_rows: list[dict[str, Any]] = []
    for ticker, cid in IDS.items():
        ticker_shadow = [row for row in shadow if row.get("candidate_id") == cid]
        ticker_strategy = [row for row in strategy if row.get("candidate_id") == cid]
        ticker_intents = [
            row for row in slot_events
            if row.get("candidate_id") == cid and row.get("event") == "DASHBOARD_REAL_BUY_INTENT"
        ]
        ticker_pending = pending_rows[ticker]
        ticker_shadow_events = [row for row in shadow_events if row.get("candidate_id") == cid]
        dashboard_row = dashboard.get(cid) or {}
        slot_row = slot_pool.get(cid) or {}
        live93_row = live93[live93["candidate_id"].eq(cid)]
        canonical_row = canonical.loc[cid]

        source_rows.extend([
            source_row(
                "ELITE_SHADOW_TRADES", "data/_system/elite_shadow_trades.jsonl", ticker,
                len(ticker_shadow), "HISTORICAL_ENTRY", True, False, True, True, False, False, False, True,
                "진입 score·threshold·ratio와 outcome은 있으나 technical component dict 없음",
            ),
            source_row(
                "ELITE_STRATEGY_SIM_TRADES", "data/_system/elite_strategy_sim_trades.jsonl", ticker,
                len(ticker_strategy), "HISTORICAL_ENTRY", len(ticker_strategy) > 0, False,
                len(ticker_strategy) > 0, len(ticker_strategy) > 0, False, False, False, len(ticker_strategy) > 0,
                "ANET 1건 ratio+outcome; BB 0건; component 없음",
            ),
            source_row(
                "ELITE_SHADOW_STATE_EVENTS", "data/_system/elite_shadow_state.json#events", ticker,
                len(ticker_shadow_events), "HISTORICAL_EVENT", False, False, False, False, False, False, False, False,
                "OPEN/CLOSE time과 position_id만 있으며 signal 필드 없음",
            ),
            source_row(
                "LIVE_ORDER_INTENTS", "data/_system/live_slots_events.jsonl", ticker,
                len(ticker_intents), "LIVE_ORDER_INTENT", False, False, False, False, False, False, False, False,
                "status·notional·execution_mode만 저장; 진입 signal snapshot 없음",
            ),
            source_row(
                "PENDING_BROKER_ORDER_STATE", "data/_system/pending_orders.json", ticker,
                len(ticker_pending), "LIVE_ORDER_STATE", False, False, False, False, False,
                len(ticker_pending) > 0, False, False,
                "candidate_id와 market/sector/VIX context는 있으나 score·threshold·components 없음",
            ),
            source_row(
                "REAL_DASHBOARD_POINT_SNAPSHOT", "data/_system/real_dashboard_buy_candidates.json", ticker,
                int(bool(dashboard_row)), "POINT_IN_TIME_SIGNAL", bool(dashboard_row.get("final_score") is not None),
                bool(dashboard_row.get("raw_score") is not None), bool(dashboard_row.get("threshold") is not None),
                bool(dashboard_row.get("ratio") is not None), isinstance(dashboard_row.get("components"), dict),
                bool(dashboard_row.get("market_score") is not None),
                isinstance(dashboard_row.get("components"), dict), False,
                "2026-07-10 point snapshot; 실제 shadow/live 주문 시점 snapshot이 아님",
            ),
            source_row(
                "LIVE_SLOTS_POINT_SNAPSHOT", "data/_system/live_slots_state.json#candidate_pool", ticker,
                int(bool(slot_row)), "POINT_IN_TIME_SIGNAL", bool(slot_row.get("final_score") is not None), False,
                bool(slot_row.get("threshold") is not None), bool(slot_row.get("ratio") is not None), False,
                bool(slot_row.get("market_score") is not None), False, False,
                "point snapshot score·threshold·ratio만; component 없음",
            ),
            source_row(
                "LIVE93_DERIVED_POINT_SCAN", "data/_system/analysis/candidate_selection_audit_20260710/live93_three_symptom_scan.csv", ticker,
                len(live93_row), "DERIVED_POINT_IN_TIME_SIGNAL", len(live93_row) > 0, False,
                len(live93_row) > 0, len(live93_row) > 0, len(live93_row) > 0, False, False, True,
                "real dashboard/evaluate point snapshot을 분해한 파생 분석; 별도 역사 진입 snapshot이 아님",
            ),
            source_row(
                "CANONICAL_STAGE3_EXIT_TRADES", "exp_batch_stage123_2009_20260616_full/tickers/*/stage3/exit_trades.jsonl", ticker,
                int(canonical_row["canonical_trade_rows"]), "HISTORICAL_BACKTEST_OUTCOME", False, False, False, False,
                False, False, False, True,
                "성과는 있으나 entry score·threshold·components 0건",
            ),
            source_row(
                "DAILY_SIGNAL_REPLAY", "data/_system/research/central_portfolio/daily_signal_replay/daily_signal_replay.jsonl", ticker,
                sum(row.get("candidate_id") == cid for row in replay), "LIMITED_REPLAY", False, False, False, False,
                False, False, False, False,
                "해당 candidate_id 기록 없음",
            ),
            source_row(
                "LIVE_AUTO_EVENTS", "data/_system/live_auto_events.jsonl", ticker,
                sum(row.get("candidate_id") == cid for row in auto_events), "LIVE_AUTO_EVENT", False, False, False,
                False, False, False, False, False,
                "해당 candidate_id 기록 없음",
            ),
            source_row(
                "APPLICATION_TEXT_LOGS", "data/logs/*; logs/*", ticker,
                0, "UNSTRUCTURED_LOG", False, False, False, False, False, False, False, False,
                "candidate_id exact match 없음",
            ),
        ])

    sources_df = pd.DataFrame(source_rows)
    sources_df.to_csv(SOURCE_OUT, index=False)

    comparison_rows: list[dict[str, Any]] = []
    for ticker, cid in IDS.items():
        trade_rows = events_df[
            events_df["candidate_id"].eq(cid)
            & events_df["record_type"].eq("SHADOW_CLOSED_TRADE_ENTRY")
        ].copy()
        point = snapshots_df[snapshots_df["candidate_id"].eq(cid)].iloc[0]
        live_intents = events_df[
            events_df["candidate_id"].eq(cid)
            & events_df["record_type"].eq("LIVE_ORDER_INTENT")
        ]
        submitted = live_intents[live_intents.get("intent_status", pd.Series(index=live_intents.index, dtype=object)).eq("submitted")]
        comparison_rows.append({
            "ticker": ticker,
            "candidate_id": cid,
            "shadow_entry_n": len(trade_rows),
            "shadow_low_ratio_n": int(trade_rows["ratio_lt_1_25"].fillna(False).sum()),
            "shadow_low_ratio_rate_pct": float(trade_rows["ratio_lt_1_25"].fillna(False).mean() * 100.0) if len(trade_rows) else None,
            "shadow_ratio_min": numeric(trade_rows["ratio"].min()) if len(trade_rows) else None,
            "shadow_ratio_max": numeric(trade_rows["ratio"].max()) if len(trade_rows) else None,
            "shadow_component_entry_snapshot_n": int(trade_rows["component_breakdown_available"].sum()) if len(trade_rows) else 0,
            "live_order_intent_n": len(live_intents),
            "live_submitted_intent_n": len(submitted),
            "live_order_time_ratio_available": False,
            "live_order_time_components_available": False,
            "point_snapshot_ratio": point["ratio"],
            "point_snapshot_threshold_excess_pct": point["threshold_excess_pct"],
            "point_snapshot_positive_component_n": point["positive_component_n"],
            "point_snapshot_top2_components": point["top2_components"],
            "point_snapshot_top2_share_pct": point["top2_share_pct"],
            "point_snapshot_technical_core_only": point["technical_core_only"],
            "point_snapshot_ce_verdict": point["ce_point_snapshot_verdict"],
            "historical_entry_ce_verdict": (
                "PARTIAL_LOW_RATIO_CONFIRMED_COMPONENT_UNKNOWN"
                if len(trade_rows) and trade_rows["ratio_lt_1_25"].fillna(False).any()
                else "UNCONFIRMED"
            ),
        })

    comparison_df = pd.DataFrame(comparison_rows)
    comparison_df.to_csv(COMPARE_OUT, index=False)

    anet = comparison_df[comparison_df["ticker"].eq("ANET")].iloc[0]
    bb = comparison_df[comparison_df["ticker"].eq("BB")].iloc[0]
    point_anet = snapshots_df[snapshots_df["ticker"].eq("ANET")].iloc[0]
    point_bb = snapshots_df[snapshots_df["ticker"].eq("BB")].iloc[0]

    summary = {
        "created_at": datetime.now(tz=KST).isoformat(),
        "verdict": "POINT_SNAPSHOT_CE_LIKE_BUT_ENTRY_COMPONENT_UNVERIFIABLE",
        "candidates": {
            ticker: comparison_df[comparison_df["ticker"].eq(ticker)].iloc[0].to_dict()
            for ticker in IDS
        },
        "entry_time_findings": {
            "shadow_entries_have_ratio": True,
            "shadow_entries_have_components": False,
            "live_order_intents_have_ratio": False,
            "live_order_intents_have_components": False,
            "actual_entry_component_concentration_verifiable": False,
        },
        "point_snapshot_findings": {
            "snapshot_time_utc": dashboard_updated_at,
            "ANET": {
                "ratio": point_anet["ratio"],
                "positive_components": 2,
                "top2": point_anet["top2_components"],
                "top2_share_pct": point_anet["top2_share_pct"],
                "technical_core_only": bool(point_anet["technical_core_only"]),
            },
            "BB": {
                "ratio": point_bb["ratio"],
                "positive_components": 2,
                "top2": point_bb["top2_components"],
                "top2_share_pct": point_bb["top2_share_pct"],
                "technical_core_only": bool(point_bb["technical_core_only"]),
            },
            "comparison": "두 후보 모두 RSI+MA 두 지표가 양수 기여 100%를 차지하고 ratio<1.25; point snapshot에서는 사실상 동일한 CE형",
        },
        "difference": {
            "ratio_BB_minus_ANET": float(point_bb["ratio"] - point_anet["ratio"]),
            "threshold_excess_pct_ANET": point_anet["threshold_excess_pct"],
            "threshold_excess_pct_BB": point_bb["threshold_excess_pct"],
            "ANET_is_closer_to_threshold": bool(point_anet["ratio"] < point_bb["ratio"]),
            "signal_composition_discriminates_direction": False,
        },
        "interpretation": (
            "ANET과 BB의 2026-07-10 full point snapshot은 모두 두 technical core 지표에 100% 집중되고 임계 대비 14.6~17.9%만 초과했다. "
            "그러나 실제 shadow/live entry 시점 component가 저장되지 않아 그 순간에도 같은 집중도였다고 단정할 수 없다."
        ),
        "no_source_change": True,
        "operational_code_change": False,
        "config_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    first_anet = events_df[
        events_df["candidate_id"].eq(IDS["ANET"])
        & events_df["record_type"].eq("SHADOW_CLOSED_TRADE_ENTRY")
    ].sort_values("signal_time_utc").iloc[0]
    first_bb = events_df[
        events_df["candidate_id"].eq(IDS["BB"])
        & events_df["record_type"].eq("SHADOW_CLOSED_TRADE_ENTRY")
    ].sort_values("signal_time_utc").iloc[0]

    lines = [
        "# ANET·BB 진입 시점 신호 구성 확인",
        "",
        "- 조사 방식: 기존 로그·상태·snapshot read-only",
        "- 최종 판정: **진입 시점 component 확인 불가 / 2026-07-10 point snapshot에서는 두 후보 모두 CE형 확인**",
        "- 운영·라이브·원본 코드·설정 변경: 0건",
        "",
        "## 1. 핵심 결론",
        "",
        "실제 과거 진입 기록에서 확인 가능한 것은 `score·threshold·ratio`까지다. Technical component dict는 ANET·BB 모든 shadow 진입에서 저장되지 않았다.",
        "",
        "실제 live 주문 intent와 pending order에도 score·threshold·ratio·component가 없다. 따라서 실제 주문 제출 순간의 Top2 집중도는 확인할 수 없다.",
        "",
        "다만 2026-07-10 real-dashboard/live93 point snapshot에는 full component가 남아 있다. 이 시점에서는 두 후보 모두 RSI와 MA 두 지표만 양수이고 Top2가 전체 양수 기여의 100%를 차지하며 ratio도 1.25 미만이다.",
        "",
        "## 2. 최초 shadow 신호",
        "",
        "| 후보 | 최초 신호 KST | score | threshold | ratio | ratio<1.25 | component |",
        "|---|---|---:|---:|---:|---|---|",
        f"| ANET | {first_anet['signal_time_kst']} | {first_anet['score']:.6f} | {first_anet['threshold']:.6f} | {first_anet['ratio']:.6f} | YES | 저장 안 됨 |",
        f"| BB | {first_bb['signal_time_kst']} | {first_bb['score']:.6f} | {first_bb['threshold']:.6f} | {first_bb['ratio']:.6f} | YES | 저장 안 됨 |",
        "",
        "최초 두 신호 모두 임계를 넘겼지만 ratio는 1.25 미만이었다. 다만 component가 없으므로 이 진입들이 RSI+MA 몰빵이었는지, news/event 보너스를 포함했는지는 확인할 수 없다.",
        "",
        "## 3. 전체 shadow 진입 ratio",
        "",
        "| 후보 | shadow 진입 | ratio<1.25 | 비율 | ratio 범위 | component snapshot |",
        "|---|---:|---:|---:|---:|---:|",
        f"| ANET | {int(anet['shadow_entry_n'])} | {int(anet['shadow_low_ratio_n'])} | {anet['shadow_low_ratio_rate_pct']:.2f}% | {anet['shadow_ratio_min']:.6f}~{anet['shadow_ratio_max']:.6f} | 0 |",
        f"| BB | {int(bb['shadow_entry_n'])} | {int(bb['shadow_low_ratio_n'])} | {bb['shadow_low_ratio_rate_pct']:.2f}% | {bb['shadow_ratio_min']:.6f}~{bb['shadow_ratio_max']:.6f} | 0 |",
        "",
        "ANET은 6건 중 5건이 저ratio였고 한 건은 score가 5.025097로 올라 ratio 1.904102였다. 그 추가 2점의 component 출처는 로그에 없어 news/event/기타 보너스로 단정할 수 없다.",
        "",
        "BB는 4건 모두 ratio 1.178711로 저ratio였다.",
        "",
        "## 4. Full component가 있는 point snapshot",
        "",
        f"Snapshot 시각: `{dashboard_updated_at}` — 실제 진입 시각이 아니라 2026-07-10 재평가 시점이다.",
        "",
        "| 항목 | ANET | BB |",
        "|---|---:|---:|",
        f"| final score | {point_anet['score']:.6f} | {point_bb['score']:.6f} |",
        f"| raw score | {point_anet['raw_score']:.6f} | {point_bb['raw_score']:.6f} |",
        f"| threshold | {point_anet['threshold']:.6f} | {point_bb['threshold']:.6f} |",
        f"| ratio | {point_anet['ratio']:.6f} | {point_bb['ratio']:.6f} |",
        f"| 임계 초과율 | {point_anet['threshold_excess_pct']:.2f}% | {point_bb['threshold_excess_pct']:.2f}% |",
        f"| MA | {point_anet['component_ma_align']:.6f} | {point_bb['component_ma_align']:.6f} |",
        f"| MACD | {point_anet['component_macd']:.6f} | {point_bb['component_macd']:.6f} |",
        f"| RSI | {point_anet['component_rsi']:.6f} | {point_bb['component_rsi']:.6f} |",
        f"| BB | {point_anet['component_bb']:.6f} | {point_bb['component_bb']:.6f} |",
        f"| Volume | {point_anet['component_volume']:.6f} | {point_bb['component_volume']:.6f} |",
        f"| News | {point_anet['component_news']:.6f} | {point_bb['component_news']:.6f} |",
        f"| News topics | {point_anet['component_news_topics']:.6f} | {point_bb['component_news_topics']:.6f} |",
        f"| Events | {point_anet['component_events']:.6f} | {point_bb['component_events']:.6f} |",
        f"| 양수 component 수 | {int(point_anet['positive_component_n'])} | {int(point_bb['positive_component_n'])} |",
        f"| Top2 | {point_anet['top2_components']} | {point_bb['top2_components']} |",
        f"| Top2 집중도 | {point_anet['top2_share_pct']:.2f}% | {point_bb['top2_share_pct']:.2f}% |",
        "| 진입 원천 | technical core only | technical core only |",
        "",
        "이 point snapshot에서는 둘 다 news·event·폭락 보너스가 아니라 RSI+MA technical core만으로 진입 조건을 넘었다.",
        "",
        "## 5. CE형 증상 판정",
        "",
        "### 진입 시점",
        "",
        "- ANET: ratio 저점유는 5/6건에서 확인되지만 component 집중도는 확인 불가 — `PARTIAL`",
        "- BB: ratio 저점유는 4/4건에서 확인되지만 component 집중도는 확인 불가 — `PARTIAL`",
        "- 실제 live 주문 제출 시점: ratio와 component 모두 없음 — `UNVERIFIABLE`",
        "",
        "### 2026-07-10 point snapshot",
        "",
        "- ANET: ratio 1.1463, Top2 100%, 양수 지표 2개 — `CE_LIKE_CONFIRMED_AT_POINT_SNAPSHOT`",
        "- BB: ratio 1.1787, Top2 100%, 양수 지표 2개 — `CE_LIKE_CONFIRMED_AT_POINT_SNAPSHOT`",
        "",
        "## 6. ANET과 BB 비교",
        "",
        "두 후보의 full point snapshot 구성은 사실상 같다.",
        "",
        "- 양수 technical 지표: 둘 다 RSI+MA 두 개",
        "- Top2 집중도: 둘 다 100%",
        "- news/event 보너스: 둘 다 0",
        "- ratio: ANET 1.1463, BB 1.1787",
        "- ANET이 임계에 약간 더 가까움: 임계 초과 14.63% 대 BB 17.87%",
        "",
        "따라서 사용자가 제시한 ANET 상승 방향과 BB 하락 방향은 이 point snapshot의 몰빵·턱걸이 정도로 구분되지 않는다. 신호 구성은 매우 비슷한데 결과 방향이 갈린 사례다.",
        "",
        "단, 이는 실제 각 진입 순간의 component 비교가 아니라 2026-07-10 동일 시점 snapshot 비교다.",
        "",
        "## 7. 데이터 부재 항목",
        "",
        "확인할 수 없는 항목:",
        "",
        "- 모든 shadow entry의 component별 기여도",
        "- 모든 shadow entry의 raw_score와 market adjustment 분해",
        "- ANET ratio 1.9041 거래의 추가 2점 출처",
        "- 2026-07-09 live 주문 제출 시점의 score·threshold·ratio",
        "- 2026-07-09 live 주문 제출 시점의 Top2·news/event bonus",
        "",
        "현재 point snapshot 값을 과거 진입 시점에 소급 적용하지 않았다.",
        "",
        "## 8. 소스 요약",
        "",
        "- `elite_shadow_trades.jsonl`: 역사 진입 ratio+outcome, component 없음",
        "- `elite_strategy_sim_trades.jsonl`: ANET 1건 ratio+outcome, component 없음",
        "- `elite_shadow_state.json`: OPEN/CLOSE 시각만",
        "- `live_slots_events.jsonl`: live intent status·notional만",
        "- `pending_orders.json`: market/sector/VIX context만",
        "- `real_dashboard_buy_candidates.json`: 2026-07-10 full point component",
        "- `live_slots_state.json`: point score·threshold·ratio, component 없음",
        "- `live93_three_symptom_scan.csv`: full point component 파생 분석",
        "- Stage3 canonical exit trades: outcome만 있고 entry signal 없음",
        "- daily signal replay·live auto events·텍스트 로그: 해당 candidate 기록 없음",
        "",
        "## 9. 산출물",
        "",
        f"- `{SOURCE_OUT.name}`",
        f"- `{EVENT_OUT.name}`",
        f"- `{SNAPSHOT_OUT.name}`",
        f"- `{COMPARE_OUT.name}`",
        f"- `{SUMMARY_OUT.name}`",
    ]
    READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
