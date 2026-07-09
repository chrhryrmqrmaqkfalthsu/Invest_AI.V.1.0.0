#!/usr/bin/env bash
set -u

BASE=/home/g3000kkw/kingmaker
PY="$BASE/venv/bin/python"
DAEMON_SCRIPT="$BASE/data/_system/ops/live_candidate_slots.py"
STATE="$BASE/data/_system/live_slots_state.json"
LOG="$BASE/logs/live_candidate_slots_guard.log"
DAEMON_LOG="$BASE/logs/live_candidate_slots_daemon_guard.log"
DAEMON_ERR="$BASE/logs/live_candidate_slots_daemon_guard.err.log"
PATTERN="data/_system/ops/live_candidate_slots.py daemon --interval 60"

mkdir -p "$BASE/logs"
cd "$BASE" || exit 1

now() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(now)] $*" >> "$LOG"; }

is_regular_hours() {
  "$PY" - <<'PY' >/tmp/km_live_candidate_regular.txt 2>/dev/null
from engine.live.regular_hours_gate import regular_hours_snapshot
snap = regular_hours_snapshot()
print('1' if snap.get('allow_decision') else '0')
PY
  grep -q '^1$' /tmp/km_live_candidate_regular.txt
}

state_age_sec() {
  if [ ! -e "$STATE" ]; then
    echo 999999
    return
  fi
  "$PY" - <<'PY'
import json, time
from datetime import datetime, timezone
from pathlib import Path
p=Path('/home/g3000kkw/kingmaker/data/_system/live_slots_state.json')
try:
    d=json.loads(p.read_text())
    ts=((d.get('last_refresh') or {}).get('time') or d.get('updated_at') or '')
    dt=datetime.fromisoformat(str(ts).replace('Z','+00:00'))
    if dt.tzinfo is None:
        dt=dt.replace(tzinfo=timezone.utc)
    print(max(0, int(time.time() - dt.timestamp())))
except Exception:
    print(999999)
PY
}

start_daemon() {
  log "starting live candidate slots daemon"
  rm -f "$BASE/data/_system/live_slots_tick.lock"
  PYTHONPATH="$BASE" nohup "$PY" "$DAEMON_SCRIPT" daemon --interval 60 >> "$DAEMON_LOG" 2>> "$DAEMON_ERR" &
  log "daemon start requested pid=$!"
}

restart_daemon() {
  log "restarting live candidate slots daemon: $*"
  pkill -f "$PATTERN" 2>/dev/null || true
  sleep 2
  start_daemon
}

if ! pgrep -f "$PATTERN" >/dev/null 2>&1; then
  start_daemon
  exit 0
fi

if is_regular_hours; then
  age=$(state_age_sec)
  if [ "$age" -gt 300 ]; then
    restart_daemon "state stale age=${age}s during regular hours"
  else
    log "ok running; state_age=${age}s"
  fi
else
  log "ok running; outside regular-hours gate"
fi
