#!/usr/bin/env python3
"""실험 검증: 유형별 고정 TTL + 선형 decay 보존 로직.

외부 API, 주문, 백테스트 호출 없음. CSV/캐시/로그 기반 읽기 전용 재생만 수행.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from datetime import datetime, timedelta
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.research._exp_event_decay_context import (  # noqa: E402
    EVENT_TTL_DAYS,
    DEFAULT_EVENT_TTL_DAYS,
    merge_active_events_with_decay,
    event_flags_from_active_events,
    trading_days_elapsed,
)

OUT_DIR = ROOT / "exp_event_decay_20260613"
MARKET_HISTORY = ROOT / "data/_system/market_history.csv"
REPORT = OUT_DIR / "event_decay_validation_report.md"
CSV_OUT = OUT_DIR / "event_decay_replay.csv"


def ev(event_type, impact, market_impact="악재", title="synthetic observed event"):
    return {
        "match_count": 1,
        "total_impact_score": round(float(impact), 2),
        "market_impact": market_impact,
        "affected_sectors": ["금융"] if "금리" in event_type else [],
        "articles": [
            {
                "title": title,
                "source": "llm_news_cache/log replay",
                "impact_score": impact,
                "confidence": "observed_or_replay",
                "timeframe": "단기",
                "weighted_score": round(float(impact), 2),
                "reasoning": "experiment replay input",
                "url": "",
                "sanity_corrected": False,
            }
        ],
    }


def score_with_event(price_score, event_adj):
    return round(max(min(price_score + event_adj, 100.0), 0.0), 1)


def replay_volatility_case():
    """6/11 금리 이벤트 휘발 구간 재생."""
    price_score = 89.8
    rows = [
        {
            "ts": "2026-06-11T19:58:47",
            "old_events": {
                "인플레이션": ev("인플레이션", -7.00, "강한_악재", "WSJ CPI inflation replay"),
                "금리정책_인상": ev("금리정책_인상", -2.94, "강한_악재", "Bloomberg Fed hike replay"),
            },
            "old_event_adj": -9.9,
            "note": "observed: 후보 2건, event_adj=-9.90",
        },
        {
            "ts": "2026-06-12T00:58:44",
            "old_events": {
                "인플레이션": ev("인플레이션", -7.00, "강한_악재", "WSJ CPI inflation cache hit"),
                "금리정책_인상": ev("금리정책_인상", -2.94, "강한_악재", "Bloomberg Fed hike cache hit"),
            },
            "old_event_adj": -9.9,
            "note": "observed: 캐시 히트 2건, event_adj=-9.90",
        },
        {
            "ts": "2026-06-12T01:58:44",
            "old_events": {
                "지정학_긴장": ev("지정학_긴장", -2.50, "악재", "geopolitical replay"),
            },
            "old_event_adj": -2.5,
            "note": "observed: 후보 1건, event_adj=-2.50",
        },
        {
            "ts": "2026-06-12T13:58:44",
            "old_events": {},
            "old_event_adj": 0.0,
            "note": "observed: 키워드 매칭 후보 없음",
        },
        {
            "ts": "2026-06-12T15:58:41",
            "old_events": {},
            "old_event_adj": 0.0,
            "note": "observed: market_state active_events={}",
        },
        {
            "ts": "2026-06-13T15:58:41",
            "old_events": {},
            "old_event_adj": 0.0,
            "note": "what-if: 신규 후보 없음, 토요일",
        },
        {
            "ts": "2026-06-25T15:58:41",
            "old_events": {},
            "old_event_adj": 0.0,
            "note": "what-if: TTL 직전 0.1 가중 확인",
        },
        {
            "ts": "2026-06-26T15:58:41",
            "old_events": {},
            "old_event_adj": 0.0,
            "note": "what-if: TTL 도달 정상 소멸 확인",
        },
    ]

    prev_events = None
    prev_ts = None
    out = []
    for row in rows:
        adj, active, debug = merge_active_events_with_decay(
            previous_active_events=prev_events,
            previous_timestamp=prev_ts,
            new_active_events=row["old_events"],
            now_timestamp=row["ts"],
            market_history_path=MARKET_HISTORY,
        )
        meta = {
            k: {
                "impact": v.get("total_impact_score"),
                "ttl": (v.get("decay_meta") or {}).get("ttl_days"),
                "elapsed": (v.get("decay_meta") or {}).get("elapsed_trading_days"),
                "weight": (v.get("decay_meta") or {}).get("decay_weight"),
            }
            for k, v in active.items()
        }
        out.append({
            "timestamp": row["ts"],
            "old_active_events": ";".join(row["old_events"].keys()) or "{}",
            "old_event_adj": row["old_event_adj"],
            "old_score": score_with_event(price_score, row["old_event_adj"]),
            "decay_active_events": ";".join(active.keys()) or "{}",
            "decay_event_adj": adj,
            "decay_score": score_with_event(price_score, adj),
            "decay_meta": json.dumps(meta, ensure_ascii=False, sort_keys=True),
            "debug": json.dumps(debug, ensure_ascii=False, sort_keys=True),
            "note": row["note"],
        })
        prev_events = active
        prev_ts = row["ts"]
    return out


def overpreservation_samples():
    scenarios = [
        ("약한_금리_인상_-2.5", "금리정책_인상", -2.5),
        ("중간_금리_인상_-5.0", "금리정책_인상", -5.0),
        ("강한_금리_인상_-7.0", "금리정책_인상", -7.0),
        ("연준발언_-5.0", "연준발언", -5.0),
        ("지정학_기본_-5.0", "지정학_긴장", -5.0),
    ]
    rows = []
    start = "2026-06-11T15:58:41"
    for label, event_type, impact in scenarios:
        previous = None
        prev_ts = None
        for t in [0, 1, 2, 3, 5, 7, 10, 14, 15]:
            now = (datetime.fromisoformat(start) + timedelta(days=t)).isoformat()
            new = {event_type: ev(event_type, impact, "악재", label)} if t == 0 else {}
            adj, active, debug = merge_active_events_with_decay(previous, prev_ts, new, now, MARKET_HISTORY)
            meta = next(iter(active.values())).get("decay_meta", {}) if active else {}
            rows.append({
                "scenario": label,
                "event_type": event_type,
                "calendar_day": t,
                "event_adj": adj,
                "active": bool(active),
                "elapsed_trading_days": meta.get("elapsed_trading_days", "expired"),
                "decay_weight": meta.get("decay_weight", 0.0),
                "ttl_days": meta.get("ttl_days", "expired"),
                "method": json.dumps(debug.get("methods", {}), ensure_ascii=False, sort_keys=True),
            })
            previous = active
            prev_ts = now
    return rows


def reader_compatibility(active_events):
    flags = event_flags_from_active_events(active_events)
    required = [
        "has_war", "has_rate_hike", "has_rate_cut", "has_regulation_risk",
        "has_trade_conflict", "has_supply_chain", "has_geopolitical",
        "has_inflation", "has_fed_statement",
    ]
    return all(k in flags and isinstance(flags[k], int) for k in required), flags


def write_csv(rows):
    fields = [
        "timestamp", "old_active_events", "old_event_adj", "old_score",
        "decay_active_events", "decay_event_adj", "decay_score", "decay_meta", "debug", "note",
    ]
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    replay = replay_volatility_case()
    write_csv(replay)
    over = overpreservation_samples()
    compat_ok, flags = reader_compatibility({
        "금리정책_인상": ev("금리정책_인상", -2.0),
        "인플레이션": ev("인플레이션", -3.0),
        "지정학_긴장": ev("지정학_긴장", -1.0),
    })

    consistency = []
    for r in replay:
        active = r["decay_active_events"] != "{}"
        adj = float(r["decay_event_adj"])
        consistency.append((r["timestamp"], active, adj, (not active and adj == 0.0) or (active and adj != 0.0)))

    report = []
    report.append("# Event decay experiment validation\n")
    report.append("## TTL table\n")
    for k, v in EVENT_TTL_DAYS.items():
        report.append(f"- {k}: {v} trading days\n")
    report.append(f"- default: {DEFAULT_EVENT_TTL_DAYS} trading days\n")
    report.append("\nDecay: weight = max(0, (TTL - elapsed_trading_days) / TTL).\n")

    report.append("\n## 6/11~6/13 replay\n")
    report.append("| timestamp | old events | old adj | decay events | decay adj | decay meta summary | note |\n")
    report.append("|---|---:|---:|---:|---:|---|---|\n")
    for r in replay:
        meta = json.loads(r["decay_meta"])
        summary = "; ".join(
            f"{k}:impact={v['impact']},elapsed={v['elapsed']},w={v['weight']},ttl={v['ttl']}"
            for k, v in meta.items()
        ) or "{}"
        report.append(
            f"| {r['timestamp']} | {r['old_active_events']} | {r['old_event_adj']} | "
            f"{r['decay_active_events']} | {r['decay_event_adj']} | {summary} | {r['note']} |\n"
        )

    report.append("\n## Over-preservation samples\n")
    report.append("| scenario | calendar day | active | elapsed trading days | weight | event_adj |\n")
    report.append("|---|---:|---:|---:|---:|---:|\n")
    for r in over:
        report.append(
            f"| {r['scenario']} | {r['calendar_day']} | {r['active']} | "
            f"{r['elapsed_trading_days']} | {r['decay_weight']} | {r['event_adj']} |\n"
        )

    report.append("\n## Reader compatibility\n")
    report.append(f"- ok: {compat_ok}\n")
    report.append(f"- flags: `{json.dumps(flags, ensure_ascii=False, sort_keys=True)}`\n")

    report.append("\n## State consistency\n")
    for ts, active, adj, ok in consistency:
        report.append(f"- {ts}: active={active}, event_adj={adj}, ok={ok}\n")

    elapsed_611_612, method_611_612 = trading_days_elapsed("2026-06-11T19:58:47", "2026-06-12T15:58:41", MARKET_HISTORY)
    report.append("\n## Calendar note\n")
    report.append(
        f"- 2026-06-11 19:58 -> 2026-06-12 15:58 elapsed={elapsed_611_612}, method={method_611_612}. "
        "market_history.csv ends at 2026-06-11, so 6/12+ replay uses weekday fallback.\n"
    )

    REPORT.write_text("".join(report), encoding="utf-8")
    print(REPORT)
    print(CSV_OUT)
    print("compat_ok", compat_ok)
    print("consistency_ok", all(x[-1] for x in consistency))


if __name__ == "__main__":
    main()
