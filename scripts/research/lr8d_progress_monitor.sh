#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

D="${1:-data/_system/research/lr8d_abcd_20260608}"
INTERVAL="${LR8D_MONITOR_INTERVAL_SECONDS:-1800}"
EXPECTED_TOPN="${LR8D_EXPECTED_TOPN_ROWS:-340}"
EXPECTED_SHARDS="${LR8D_EXPECTED_SHARDS:-8}"
DISABLE_TELEGRAM="${LR8D_MONITOR_DISABLE_TELEGRAM:-0}"
RUN_PATTERN="scripts/research/run_lr8d_abcd_fulluniverse.py"
TOPN="$D/lr8d_abcd_topn.jsonl"
RULEBOOKS="$D/lr8d_abcd_topn_rulebooks.jsonl"
TRADES="$D/lr8d_abcd_trades.jsonl"
SURVIVORS="$D/lr8d_abcd_survivors.jsonl"

mkdir -p "$D"

# Load TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the project .env when they
# are not already exported by the caller. Do not echo secrets.
if [ "$DISABLE_TELEGRAM" != "1" ] && { [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; } && [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

_rows() {
  local f="$1"
  if [ -f "$f" ]; then
    wc -l < "$f" | tr -d ' '
  else
    echo 0
  fi
}

_size() {
  du -sh "$1" 2>/dev/null | awk '{print $1}' || echo "0"
}

_free_space() {
  df -h . 2>/dev/null | awk 'NR==2{print $4}' || echo "unknown"
}

_alive() {
  local matches
  matches="$(pgrep -af "$RUN_PATTERN" 2>/dev/null || true)"
  if [ -z "$matches" ]; then
    echo 0
    return 0
  fi
  printf '%s\n' "$matches" | grep -v "lr8d_progress_monitor" | grep -v "^$" | wc -l | tr -d ' ' || echo 0
}

_recent_progress() {
  local logs
  logs=$(ls "$D"/lr8d_abcd_shard_*.log 2>/dev/null || true)
  if [ -z "$logs" ]; then
    echo "최근 로그 없음"
    return 0
  fi
  tail -n 1 $logs 2>/dev/null | tail -8 | sed 's/^/  /' || echo "최근 로그 읽기 실패"
}

_dump_status() {
  if [ ! -s "$TRADES" ]; then
    echo "trade dump: trades 파일 없음"
    return 0
  fi
  "$ROOT/venv/bin/python" - "$TRADES" <<'PY' 2>/dev/null || true
import json, sys
path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        row = json.loads(f.readline())
    trades = row.get("trades") or []
    t = trades[0] if trades else {}
    entry_cols = (((t.get("entry_context_full") or {}).get("row") or {}).get("columns") or {})
    print("trade dump: "
          f"rulebook_full={'OK' if t.get('rulebook_full') else 'MISSING'}, "
          f"entry_context_full={'OK' if t.get('entry_context_full') else 'MISSING'}, "
          f"exit_context_full={'OK' if t.get('exit_context_full') else 'MISSING'}, "
          f"holding_path_rows={t.get('holding_path_row_count')}, "
          f"rulebook_fields={len(t.get('rulebook_full') or {})}, "
          f"entry_columns={len(entry_cols)}")
except Exception as exc:
    print(f"trade dump: CHECK_FAILED {exc}")
PY
}

_send() {
  local msg="$1"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $msg"
  if [ "$DISABLE_TELEGRAM" != "1" ] && [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    curl -fsS --connect-timeout 5 --max-time 10 \
      -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
  fi
}

_report_message() {
  local alive topn rb tr survivors sz free dump recent complete_mark
  alive="$(_alive)"
  topn="$(_rows "$TOPN")"
  rb="$(_rows "$RULEBOOKS")"
  tr="$(_rows "$TRADES")"
  survivors="$(_rows "$SURVIVORS")"
  sz="$(_size "$D")"
  free="$(_free_space)"
  dump="$(_dump_status)"
  recent="$(_recent_progress)"
  if [ "$topn" -ge "$EXPECTED_TOPN" ]; then
    complete_mark="topn 완료"
  else
    complete_mark="topn 진행"
  fi
  cat <<MSG
LR8D A+B+C+D ${complete_mark}
샤드 alive: ${alive}/${EXPECTED_SHARDS}
topn: ${topn}/${EXPECTED_TOPN}
rulebooks rows: ${rb}
trades rows: ${tr}
survivors rows: ${survivors}
디렉터리 크기: ${sz}
디스크 여유: ${free}
${dump}
최근 로그:
${recent}
MSG
}

_send "🚀 LR8D A+B+C+D 풀런 모니터 시작. interval=${INTERVAL}s, out=${D}"

while true; do
  msg="$(_report_message)"
  alive="$(_alive)"
  topn="$(_rows "$TOPN")"

  if [ "$alive" -eq 0 ]; then
    if [ "$topn" -ge "$EXPECTED_TOPN" ]; then
      _send "✅ LR8D 풀런 종료 감지.\n${msg}\n포스트런 분석 준비됨."
    else
      _send "⚠️ LR8D 샤드가 모두 종료됐지만 topn 미완료입니다.\n${msg}\n로그 확인 필요."
    fi
    break
  fi

  _send "⏳ LR8D 진행중\n${msg}"
  sleep "$INTERVAL"
done
