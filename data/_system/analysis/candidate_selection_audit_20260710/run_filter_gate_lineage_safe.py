from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]
for path in (ROOT, HERE):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

import run_filter_gate_lineage as lineage

if __name__ == "__main__":
    raise SystemExit(lineage.main())
