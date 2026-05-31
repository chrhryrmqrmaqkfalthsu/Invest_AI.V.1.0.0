#!/usr/bin/env python3
"""지정 종목 1개의 2025-06~2026-05 (12개월) 뉴스만 받기. 인자: 티커."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import download_news as dl

TICKER = sys.argv[1] if len(sys.argv) > 1 else None
if not TICKER:
    raise SystemExit("사용법: python fetch_one_recent.py TICKER")

MONTHS = [(2025, m) for m in range(6, 13)] + [(2026, m) for m in range(1, 6)]
print(f"대상: {TICKER}  {len(MONTHS)}개월")
got = skip = fail = 0
for (y, m) in MONTHS:
    if dl.already_have(TICKER, y, m):
        print(f"  skip {y}-{m:02d}"); skip += 1; continue
    d = dl.fetch_month(TICKER, y, m)
    if d is not None:
        p = dl.save_gz(d, TICKER, y, m)
        feed = len(d.get("feed", [])) if isinstance(d, dict) else 0
        print(f"  OK {y}-{m:02d} 기사{feed}건"); got += 1
    else:
        print(f"  FAIL {y}-{m:02d}"); fail += 1
    time.sleep(2.0)
print(f"완료: 받음{got} skip{skip} 실패{fail}")