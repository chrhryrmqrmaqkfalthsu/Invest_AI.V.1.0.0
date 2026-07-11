#!/usr/bin/env bash
set -u

BASE=/home/g3000kkw/kingmaker
PY="$BASE/venv/bin/python"
DAEMON_SCRIPT="$BASE/data/_system/ops/live_candidate_slots.py"
STATE="$BASE/data/_system/live_slots_state.json"
PIDFILE="$BASE/data/_system/live_candidate_slots_daemon.pid"
LOG="$BASE/logs/live_candidate_slots_guard.log"
DAEMON_LOG="$BASE/logs/live_candidate_slots_daemon_guard.log"
DAEMON_ERR="$BASE/logs/live_candidate_slots_daemon_guard.err.log"

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

exact_daemon_pids() {
  "$PY" - "$PY" "$DAEMON_SCRIPT" <<'PY'
import os
import sys
from pathlib import Path

expected = [sys.argv[1], sys.argv[2], "daemon", "--interval", "60"]
seen = set()

pidfile = Path('/home/g3000kkw/kingmaker/data/_system/live_candidate_slots_daemon.pid')
try:
    text = pidfile.read_text(encoding='utf-8').strip()
    if text.isdigit():
        seen.add(int(text))
except Exception:
    pass

for proc in Path('/proc').iterdir():
    if proc.name.isdigit():
        seen.add(int(proc.name))

for pid in sorted(seen):
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
        argv = [part.decode('utf-8', errors='replace') for part in raw.split(b'\0') if part]
    except Exception:
        continue
    if argv == expected:
        print(pid)
PY
}

daemon_is_running() {
  [ -n "$(exact_daemon_pids)" ]
}

start_daemon() {
  log "starting live candidate slots daemon"
  rm -f "$BASE/data/_system/live_slots_tick.lock"
  PYTHONPATH="$BASE" nohup "$PY" "$DAEMON_SCRIPT" daemon --interval 60 >> "$DAEMON_LOG" 2>> "$DAEMON_ERR" &
  pid=$!
  printf '%s\n' "$pid" > "$PIDFILE"
  sleep 1
  if exact_daemon_pids | grep -qx "$pid"; then
    log "daemon started pid=$pid"
    return 0
  fi
  rm -f "$PIDFILE"
  log "daemon start failed pid=$pid"
  return 1
}

stop_daemons() {
  pids=$(exact_daemon_pids)
  if [ -z "$pids" ]; then
    rm -f "$PIDFILE"
    return 0
  fi
  for pid in $pids; do
    kill -TERM "$pid" 2>/dev/null || true
  done
  for _ in $(seq 1 20); do
    remaining=$(exact_daemon_pids)
    [ -z "$remaining" ] && break
    sleep 1
  done
  remaining=$(exact_daemon_pids)
  if [ -n "$remaining" ]; then
    for pid in $remaining; do
      kill -KILL "$pid" 2>/dev/null || true
    done
  fi
  rm -f "$PIDFILE"
}

restart_daemon() {
  log "restarting live candidate slots daemon: $*"
  stop_daemons
  sleep 2
  start_daemon
}

if ! daemon_is_running; then
  start_daemon
  exit $?
fi

pids=$(exact_daemon_pids | tr '\n' ' ')
if is_regular_hours; then
  age=$(state_age_sec)
  if [ "$age" -gt 300 ]; then
    restart_daemon "state stale age=${age}s during regular hours"
  else
    log "ok running pids=${pids}; state_age=${age}s"
  fi
else
  log "ok running pids=${pids}; outside regular-hours gate"
fi
