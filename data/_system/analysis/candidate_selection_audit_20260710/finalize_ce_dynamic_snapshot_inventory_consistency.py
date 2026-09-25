from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
SOURCE = OUT / "ce_dynamic_snapshot_source_coverage.csv"
QUALITY = OUT / "ce_dynamic_snapshot_quality.csv"

source = pd.read_csv(SOURCE, low_memory=False)
mask = source["source_name"].eq("LIVE_SLOTS_EVENTS")
record_n = int(source.loc[mask, "record_n"].iloc[0])
note = f"{record_n:,} operational refresh/buy-intent events; no structured final_score/threshold/components payload"
source.loc[mask, "note"] = note
source.to_csv(SOURCE, index=False)

quality = pd.read_csv(QUALITY, low_memory=False)
quality.loc[quality["source_name"].eq("LIVE_SLOTS_EVENTS"), "note"] = note
quality.to_csv(QUALITY, index=False)
print({"live_slots_event_record_n": record_n, "note": note})
