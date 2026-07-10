from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
AUDIT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
for path in (ROOT, AUDIT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import integrated_gate_sim_core as core

FULL = AUDIT / "sparse_indicator_entry_structure_full.csv"
PARITY = AUDIT / "sparse_indicator_entry_parity_summary.csv"
BOIL_MISSED = AUDIT / "sparse_indicator_entry_boil_n2_missed.csv"
SUMMARY = AUDIT / "sparse_indicator_entry_summary.json"


def flag(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def num(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def main() -> int:
    frame = pd.read_csv(FULL, low_memory=False)
    origins, _ = core.load_origins()
    extra = []
    event_fields = (
        "event_response_war", "event_response_rate_hike", "event_response_rate_cut",
        "event_response_geopolitical", "event_response_tariff", "event_response_export_ban",
        "event_response_earnings_shock", "event_response_oil_surge",
        "event_response_banking_crisis", "event_response_inflation",
        "event_response_fed_statement",
    )
    topic_fields = tuple(
        "weight_news_" + name for name in (
            "blockchain", "earnings", "ipo", "mergers_and_acquisitions",
            "financial_markets", "economy_fiscal", "economy_monetary",
            "economy_macro", "energy_transportation", "finance", "life_sciences",
            "manufacturing", "real_estate", "retail_wholesale", "technology",
        )
    )
    for row in origins:
        rb = row["rulebook"]
        positive_event = any(num(rb.get(field)) > 0 for field in event_fields)
        positive_topic = any(num(rb.get(field)) > 0 for field in topic_fields)
        extra.append({
            "candidate_id": row["candidate_id"],
            "crash_buy_enabled": flag(rb.get("crash_buy_enabled")),
            "use_news_global": flag(rb.get("use_news_global", True)),
            "weight_news_sentiment": num(rb.get("weight_news_sentiment")),
            "use_event_block": flag(rb.get("use_event_block", True)),
            "positive_event_response_exists": positive_event,
            "positive_topic_weight_exists": positive_topic,
            "noncore_positive_additive_possible": bool(
                flag(rb.get("crash_buy_enabled"))
                or (flag(rb.get("use_news_global", True)) and num(rb.get("weight_news_sentiment")) != 0)
                or positive_event or positive_topic
            ),
        })
    enriched = frame.merge(pd.DataFrame(extra), on="candidate_id", how="left", validate="one_to_one")
    enriched["core_only_risk_result_scope"] = (
        "SUFFICIENT_IF_FLAGGED_NOT_NECESSARY_IF_NOT_FLAGGED"
    )
    enriched.to_csv(FULL, index=False)
    enriched[enriched["market_cap_n1"]].to_csv(AUDIT / "sparse_indicator_entry_n1_market_cap.csv", index=False)
    enriched[enriched["market_cap_n2_or_less"]].to_csv(AUDIT / "sparse_indicator_entry_n2_market_cap.csv", index=False)
    enriched[enriched["neutral_n1"]].to_csv(AUDIT / "sparse_indicator_entry_n1_neutral.csv", index=False)
    enriched[enriched["neutral_n2_or_less"]].to_csv(AUDIT / "sparse_indicator_entry_n2_neutral.csv", index=False)
    enriched[enriched["boil_existing"]].to_csv(AUDIT / "sparse_indicator_entry_boil_parity.csv", index=False)
    enriched[enriched["ce_existing_fail"]].to_csv(AUDIT / "sparse_indicator_entry_ce_parity.csv", index=False)

    rows = []
    for defect, mask_column, count in (
        ("BOIL", "boil_existing", int(enriched["boil_existing"].sum())),
        ("CE", "ce_existing_fail", int(enriched["ce_existing_fail"].sum())),
    ):
        group = enriched[enriched[mask_column]]
        for scenario, prefix in (("neutral", "neutral"), ("market_cap", "market_cap")):
            for threshold, column in (("N1", f"{prefix}_n1"), ("N2_OR_LESS", f"{prefix}_n2_or_less")):
                captured = int(group[column].sum())
                rows.append({
                    "defect_family": defect,
                    "scenario": scenario,
                    "sparse_rule": threshold,
                    "defect_count": count,
                    "captured_count": captured,
                    "missed_count": count - captured,
                    "capture_rate_pct": captured / count * 100.0 if count else 0.0,
                })
    pd.DataFrame(rows).to_csv(PARITY, index=False)
    enriched[enriched["boil_existing"] & ~enriched["market_cap_n2_or_less"]].to_csv(BOIL_MISSED, index=False)

    payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
    payload["percentages"] = {
        "neutral_n1_pct": float(enriched["neutral_n1"].mean() * 100),
        "neutral_n2_or_less_pct": float(enriched["neutral_n2_or_less"].mean() * 100),
        "market_cap_n1_pct": float(enriched["market_cap_n1"].mean() * 100),
        "market_cap_n2_or_less_pct": float(enriched["market_cap_n2_or_less"].mean() * 100),
        "complete_market_cap_n1_pct": float(enriched[enriched["origin_complete_bool"]]["market_cap_n1"].mean() * 100),
        "complete_market_cap_n2_or_less_pct": float(enriched[enriched["origin_complete_bool"]]["market_cap_n2_or_less"].mean() * 100),
    }
    payload["noncore_additive_possible_count"] = int(enriched["noncore_positive_additive_possible"].sum())
    payload["core_only_scope"] = (
        "A flagged rule is structurally sparse-entry capable with core indicators alone. "
        "A non-flagged rule is not proven safe because positive non-core additions were fixed at zero."
    )
    payload["parity_summary_csv"] = str(PARITY.relative_to(ROOT))
    payload["boil_missed_csv"] = str(BOIL_MISSED.relative_to(ROOT))
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print({
        "rows": len(enriched),
        "noncore_positive_possible": int(enriched["noncore_positive_additive_possible"].sum()),
        "boil_missed_n2": int((enriched["boil_existing"] & ~enriched["market_cap_n2_or_less"]).sum()),
        "parity_rows": len(rows),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
