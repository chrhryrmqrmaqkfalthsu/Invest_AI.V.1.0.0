#!/usr/bin/env python3
"""AAPL 2025-06 ~ 2026-05 (12개월) 뉴스만 1회성 다운로드.
메인 download_news.py의 fetch_month/save_gz/already_have를 그대로 재사용한다.
요청 간격 2초 (메인 다운로더와 합쳐도 75/min 안전)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import download_news as dl

TICKER = "AAPL"
MONTHS = []
for y, m in [(2025, mm) for mm in range(6, 13)] + [(2026, mm) for mm in range(1, 6)]:
    MONTHS.append((y, m))

print(f"대상: {TICKER}  {len(MONTHS)}개월  {MONTHS[0]} ~ {MONTHS[-1]}")
got, skip, fail = 0, 0, 0
for (y, m) in MONTHS:
    if dl.already_have(TICKER, y, m):
        print(f"  skip {y}-{m:02d} (이미 있음)")
        skip += 1
        continue
    d = dl.fetch_month(TICKER, y, m)
    if d is not None:
        p = dl.save_gz(d, TICKER, y, m)
        feed = len(d.get("feed", [])) if isinstance(d, dict) else 0
        print(f"  OK   {y}-{m:02d}  기사 {feed}건  -> {p.name}")
        got += 1
    else:
        print(f"  FAIL {y}-{m:02d}")
        fail += 1
    time.sleep(2.0)
print(f"\n완료: 받음 {got}, 건너뜀 {skip}, 실패 {fail}")