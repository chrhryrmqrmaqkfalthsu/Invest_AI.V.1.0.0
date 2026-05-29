#!/usr/bin/env python3
"""
AlphaVantage 뉴스 일괄 다운로드
- 대상: data/_system/ticker_universe.json (6,176 종목)
- 기간: 60개월 (2020-06 ~ 2025-05)
- 저장: data/_system/ticker_news_cache/{TICKER}/av_{TICKER}_YYYYMM.json.gz
- 재개: data/_system/download_progress.json
"""
import os, sys, json, gzip, time, signal
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error

# ── 설정 ──────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
UNIVERSE_FILE = ROOT / "data/_system/ticker_universe.json"
CACHE_DIR = ROOT / "data/_system/ticker_news_cache"
PROGRESS_FILE = ROOT / "data/_system/download_progress.json"
LOG_FILE = Path("/tmp/news_download.log")

API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
if not API_KEY:
    raise SystemExit("❌ ALPHA_VANTAGE_KEY 환경변수가 필요합니다 (.env 파일 확인)")
BASE_URL = "https://www.alphavantage.co/query"

# Premium 75 req/min → 70으로 여유. interval = 60/70 = 0.857s
REQ_INTERVAL = 0.86
MAX_RETRY = 3
RETRY_WAIT = 30

# 기간: 2020-06 ~ 2025-05 (60개월)
MONTHS = []
for y in range(2020, 2026):
    for m in range(1, 13):
        if (y == 2020 and m < 6) or (y == 2025 and m > 5) or y > 2025:
            continue
        MONTHS.append((y, m))

# ── Telegram 알림 (선택적) ────────────────────────
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
def notify(msg):
    log(msg)
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        import urllib.parse
        data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data=data, timeout=10
        ).read()
    except Exception:
        pass

# ── 로그 ──────────────────────────────────────────
def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── 진행 상황 ─────────────────────────────────────
def load_progress():
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            pass
    return {"completed": [], "partial": {}, "failed": {}, "total_requests": 0, "started_at": datetime.now().isoformat()}

def save_progress(p):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, indent=2))
    tmp.replace(PROGRESS_FILE)

# ── 다운로드 ──────────────────────────────────────
def fetch_month(ticker, year, month):
    """1종목 × 1개월. 다음달 1일 0시까지 가져옴."""
    t_from = f"{year:04d}{month:02d}01T0000"
    if month == 12:
        ny, nm = year + 1, 1
    else:
        ny, nm = year, month + 1
    t_to = f"{ny:04d}{nm:02d}01T0000"
    url = (f"{BASE_URL}?function=NEWS_SENTIMENT&tickers={ticker}"
           f"&time_from={t_from}&time_to={t_to}&limit=1000&apikey={API_KEY}")

    for attempt in range(1, MAX_RETRY + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            d = json.loads(raw)
            # Rate limit / 에러 응답
            if "Information" in d and ("rate limit" in d["Information"].lower() or
                                       "premium" in d["Information"].lower()):
                log(f"  [RATE] {ticker} {year}-{month:02d}: {d['Information'][:80]}")
                time.sleep(RETRY_WAIT)
                continue
            if "Error Message" in d:
                log(f"  [ERROR] {ticker} {year}-{month:02d}: {d['Error Message'][:80]}")
                return None  # 영구 실패, 재시도 무의미
            # 성공 (items=0도 정상, 그 달에 뉴스 없음)
            return d
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            log(f"  [RETRY {attempt}/{MAX_RETRY}] {ticker} {year}-{month:02d}: {type(e).__name__}")
            time.sleep(5 * attempt)
        except Exception as e:
            log(f"  [EXC] {ticker} {year}-{month:02d}: {type(e).__name__}: {str(e)[:80]}")
            time.sleep(5)
    return None

def save_gz(data, ticker, year, month):
    ddir = CACHE_DIR / ticker
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / f"av_{ticker}_{year:04d}{month:02d}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path

def already_have(ticker, year, month):
    path = CACHE_DIR / ticker / f"av_{ticker}_{year:04d}{month:02d}.json.gz"
    return path.exists() and path.stat().st_size > 50

# ── 종료 시그널 핸들러 ────────────────────────────
STOP = False
def handle_sig(signum, frame):
    global STOP
    log(f"[SIGNAL] {signum} 수신 → 현재 종목 마치고 종료")
    STOP = True
signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)

# ── 메인 ──────────────────────────────────────────
def main():
    if not UNIVERSE_FILE.exists():
        log(f"❌ {UNIVERSE_FILE} 없음")
        sys.exit(1)
    universe = json.loads(UNIVERSE_FILE.read_text())
    tickers = [u["symbol"] for u in universe]
    log(f"=== 다운로드 시작: {len(tickers)} 종목 × {len(MONTHS)} 개월 = {len(tickers)*len(MONTHS):,} 요청 ===")

    progress = load_progress()
    completed = set(progress["completed"])
    log(f"이전 완료: {len(completed)}종 / 총 요청 누적: {progress.get('total_requests',0):,}")
    notify(f"📥 뉴스 다운로드 시작\n총 {len(tickers)}종 × 60개월\n이전 완료: {len(completed)}종")

    start_ts = time.time()
    last_notify = len(completed)
    req_count = 0

    for idx, ticker in enumerate(tickers, 1):
        if STOP:
            break
        if ticker in completed:
            continue

        ticker_start = time.time()
        ok_months = 0
        fail_months = []

        for (y, m) in MONTHS:
            if STOP:
                break
            if already_have(ticker, y, m):
                ok_months += 1
                continue

            t0 = time.time()
            d = fetch_month(ticker, y, m)
            req_count += 1
            progress["total_requests"] = progress.get("total_requests", 0) + 1

            if d is not None:
                try:
                    save_gz(d, ticker, y, m)
                    ok_months += 1
                except Exception as e:
                    log(f"  [SAVE-FAIL] {ticker} {y}-{m:02d}: {e}")
                    fail_months.append(f"{y}-{m:02d}")
            else:
                fail_months.append(f"{y}-{m:02d}")

            # Rate limit: 평균 0.86s/req 유지
            elapsed = time.time() - t0
            if elapsed < REQ_INTERVAL:
                time.sleep(REQ_INTERVAL - elapsed)

        # 종목 완료 처리
        ticker_elapsed = time.time() - ticker_start
        if ok_months == len(MONTHS):
            completed.add(ticker)
            progress["completed"] = sorted(completed)
            progress["partial"].pop(ticker, None)
        elif ok_months > 0:
            progress["partial"][ticker] = {"ok": ok_months, "failed": fail_months}
        else:
            progress["failed"][ticker] = fail_months

        # 매 종목마다 진행 저장
        save_progress(progress)

        # 로그
        rate = req_count / max(time.time() - start_ts, 1) * 60
        log(f"[{idx}/{len(tickers)}] {ticker}: {ok_months}/{len(MONTHS)}개월 ({ticker_elapsed:.1f}s) | 누적 완료 {len(completed)}종 | 속도 {rate:.1f} req/min")

        # 100종마다 Telegram
        if len(completed) >= last_notify + 100:
            last_notify = len(completed)
            remain = len(tickers) - len(completed)
            eta_h = remain * (time.time() - start_ts) / max(idx, 1) / 3600
            notify(f"📥 진행: {len(completed)}/{len(tickers)}종 완료\n"
                   f"속도: {rate:.0f} req/min\n"
                   f"실패: {len(progress.get('failed',{}))}종 / 부분: {len(progress.get('partial',{}))}종\n"
                   f"ETA: {eta_h:.1f}h")

    # 종료
    total_elapsed = time.time() - start_ts
    msg = (f"✅ 다운로드 종료\n"
           f"완료: {len(completed)}/{len(tickers)}종\n"
           f"부분: {len(progress.get('partial',{}))}종\n"
           f"실패: {len(progress.get('failed',{}))}종\n"
           f"소요: {total_elapsed/3600:.1f}h\n"
           f"누적 요청: {progress.get('total_requests',0):,}")
    log(msg)
    notify(msg)

if __name__ == "__main__":
    main()
