#!/usr/bin/env bash
# 6174종목 전체: 뉴스 최신화 → bulk swing diagnostic 순차 실행 단순 래퍼.
# - update_ticker_sentiment_recent.py는 --tickers-file이 없으므로 200개 배치 positional args로 호출한다.
# - 일부 뉴스 종목 실패(exit 1)는 로그에 남기고 계속한다.
# - bulk_swing_diagnostic.py는 --resume으로 이어가기 가능하다.
set -u
set -o pipefail

cd "$(dirname "$0")/../.."

UNIVERSE_JSON="data/_system/ticker_universe.json"
UNIVERSE_TXT="data/_system/screening_universe_all.txt"
NEWS_LOG="logs/full_screening_news.log"
DIAG_LOG="logs/full_screening_diagnostic.log"
WRAP_LOG="logs/full_screening_wrapper.log"
STATUS_JSON="data/_system/full_screening_status.json"

BATCH_SIZE="${BATCH_SIZE:-200}"
DAILY_LIMIT="${DAILY_LIMIT:-10000}"
MARKET_RESERVE="${MARKET_RESERVE:-0}"
REQUEST_INTERVAL="${REQUEST_INTERVAL:-0.86}"
PARALLEL="${PARALLEL:-8}"
TIMEOUT_SEC="${TIMEOUT_SEC:-1200}"

mkdir -p data/_system logs

write_status() {
  local stage="$1"
  local state="$2"
  local msg="${3:-}"
  env STATUS_JSON="$STATUS_JSON" STAGE="$stage" STATE="$state" MSG="$msg" venv/bin/python - <<'PY'
import json, os
from pathlib import Path
from datetime import datetime
p = Path(os.environ['STATUS_JSON'])
try:
    data = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
except Exception:
    data = {}
data.setdefault('events', [])
now = datetime.now().isoformat(timespec='seconds')
data.update({'updated_at': now, 'stage': os.environ['STAGE'], 'state': os.environ['STATE'], 'message': os.environ.get('MSG', '')})
data['events'].append({'ts': now, 'stage': data['stage'], 'state': data['state'], 'message': data['message']})
p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
PY
}

log_wrap() {
  echo "$1" | tee -a "$WRAP_LOG"
}

: > "$WRAP_LOG"
: > "$NEWS_LOG"

log_wrap "=== full screening simple wrapper started $(date -Is) ==="
write_status "wrapper" "started" "full screening simple wrapper started"

log_wrap "=== Step 1: build universe file ==="
write_status "universe" "started" "building universe txt"
venv/bin/python - <<'PY'
import json
from pathlib import Path
src = Path('data/_system/ticker_universe.json')
dst = Path('data/_system/screening_universe_all.txt')
data = json.loads(src.read_text(encoding='utf-8'))
seen = set()
tickers = []
for item in data:
    t = str(item.get('symbol', '')).strip().upper()
    if t and t not in seen:
        seen.add(t)
        tickers.append(t)
dst.write_text('\n'.join(tickers) + '\n', encoding='utf-8')
print(f'wrote {len(tickers)} tickers to {dst}')
PY
UNIVERSE_COUNT="$(wc -l < "$UNIVERSE_TXT" | tr -d ' ')"
log_wrap "universe_count=${UNIVERSE_COUNT}"
write_status "universe" "done" "universe_count=${UNIVERSE_COUNT}"

log_wrap "=== Step A: news sentiment update started $(date -Is) ==="
write_status "news" "started" "news update started"
mapfile -t ALL_TICKERS < "$UNIVERSE_TXT"
TOTAL="${#ALL_TICKERS[@]}"
BATCH_NO=0
NEWS_FAIL_BATCHES=0
NEWS_CRASH_BATCHES=0

for ((i=0; i<TOTAL; i+=BATCH_SIZE)); do
  BATCH_NO=$((BATCH_NO + 1))
  BATCH=("${ALL_TICKERS[@]:i:BATCH_SIZE}")
  echo "=== news batch ${BATCH_NO} start index=${i} size=${#BATCH[@]} $(date -Is) ===" | tee -a "$NEWS_LOG"
  set +e
  venv/bin/python scripts/news_downloader/update_ticker_sentiment_recent.py \
    --daily-limit "$DAILY_LIMIT" \
    --market-reserve "$MARKET_RESERVE" \
    --request-interval "$REQUEST_INTERVAL" \
    "${BATCH[@]}" >> "$NEWS_LOG" 2>&1
  RC=$?
  set -e
  echo "=== news batch ${BATCH_NO} exit=${RC} $(date -Is) ===" | tee -a "$NEWS_LOG"

  if tail -n 160 "$NEWS_LOG" | grep -q "QUOTA_STOP\|QUOTA_EXCEEDED"; then
    log_wrap "WARN: quota stop/exceeded appeared in news batch ${BATCH_NO}; continuing by request"
  fi

  if [ "$RC" -ne 0 ]; then
    if tail -n 120 "$NEWS_LOG" | grep -q "=== summary:"; then
      NEWS_FAIL_BATCHES=$((NEWS_FAIL_BATCHES + 1))
      log_wrap "WARN: news batch ${BATCH_NO} had ticker-level failures rc=${RC}; continuing"
    else
      NEWS_CRASH_BATCHES=$((NEWS_CRASH_BATCHES + 1))
      log_wrap "WARN: news batch ${BATCH_NO} exited rc=${RC} without visible summary; continuing by simple-mode policy"
    fi
  fi
  write_status "news" "batch_done" "batch=${BATCH_NO} rc=${RC}"
done

log_wrap "=== news sentiment update finished $(date -Is) fail_batches=${NEWS_FAIL_BATCHES} crash_like_batches=${NEWS_CRASH_BATCHES} ==="
write_status "news" "done" "fail_batches=${NEWS_FAIL_BATCHES}, crash_like_batches=${NEWS_CRASH_BATCHES}"

log_wrap "=== Step B: bulk diagnostic started $(date -Is) ==="
write_status "diagnostic" "started" "bulk diagnostic started"
venv/bin/python scripts/screening/bulk_swing_diagnostic.py \
  --tickers-file "$UNIVERSE_TXT" \
  --limit "$UNIVERSE_COUNT" \
  --parallel "$PARALLEL" \
  --timeout-sec "$TIMEOUT_SEC" \
  --resume > "$DIAG_LOG" 2>&1
DIAG_RC=$?

if [ "$DIAG_RC" -ne 0 ]; then
  log_wrap "ERROR: bulk diagnostic failed rc=${DIAG_RC}"
  write_status "diagnostic" "failed" "rc=${DIAG_RC}"
  exit "$DIAG_RC"
fi

log_wrap "=== bulk diagnostic finished $(date -Is) ==="
write_status "diagnostic" "done" "bulk diagnostic completed"
log_wrap "=== full screening simple wrapper completed $(date -Is) ==="
write_status "wrapper" "done" "full screening completed"
