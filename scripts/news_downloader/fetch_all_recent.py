#!/usr/bin/env python3
"""전 종목 2025-06 ~ 2026-05 (12개월) 뉴스 배치 다운로드.
메인 download_news.py의 fetch_month/save_gz/already_have를 재사용.
재개 가능(already_have로 중복 skip). 요청 간격 0.86초(메인과 동시 실행 금지)."""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import download_news as dl

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "data/_system/ticker_universe.json"
LOG = open("/tmp/recent_download.log", "a", encoding="utf-8")

def log(m):
    line = f"{time.strftime('%H:%M:%S')} {m}"
    print(line); LOG.write(line+"\n"); LOG.flush()

MONTHS = [(2025, m) for m in range(6, 13)] + [(2026, m) for m in range(1, 6)]

u = json.load(open(UNIVERSE))
tickers = [x["symbol"] if isinstance(x, dict) else x for x in u]
log(f"=== 전종목 최근12개월 배치 시작: {len(tickers)}종목 x {len(MONTHS)}개월 ===")

done = 0
for i, t in enumerate(tickers, 1):
    got = skip = fail = 0
    for (y, m) in MONTHS:
        if dl.already_have(t, y, m):
            skip += 1; continue
        d = dl.fetch_month(t, y, m)
        if d is not None:
            dl.save_gz(d, t, y, m); got += 1
        else:
            fail += 1
        time.sleep(0.86)
    done += 1
    log(f"[{i}/{len(tickers)}] {t}: 받음{got} skip{skip} 실패{fail}")
log("=== 배치 완료 ===")