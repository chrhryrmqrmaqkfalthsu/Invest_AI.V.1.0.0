#!/usr/bin/env bash
set -u

BASE=/home/g3000kkw/kingmaker
LOG="$BASE/logs/dashboard_guard.log"
ROUTE_SCRIPT="$BASE/scripts/ensure_caddy_dashboard_route.py"
PY="$BASE/venv/bin/python"
UVICORN="$BASE/venv/bin/uvicorn"

mkdir -p "$BASE/logs"
cd "$BASE" || exit 1

now() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }
log() { echo "[$(now)] $*" >> "$LOG"; }

health_ok() {
  curl -fsS --max-time 8 http://127.0.0.1:8001/dashboard >/tmp/km_dashboard_guard_dashboard.html 2>/dev/null \
    && grep -q 'KINGMAKER' /tmp/km_dashboard_guard_dashboard.html \
    && curl -fsS --max-time 12 http://127.0.0.1:8001/api/live/slots >/dev/null 2>&1
}

start_dashboard() {
  log "dashboard health failed; restarting api_server_candidate_only on 8001"
  pkill -f 'uvicorn api_server_candidate_only:app --host 0.0.0.0 --port 8001' 2>/dev/null || true
  sleep 2
  PYTHONPATH="$BASE" nohup "$UVICORN" api_server_candidate_only:app --host 0.0.0.0 --port 8001 \
    >> "$BASE/logs/api_server_candidate_only_8001_guard.log" \
    2>> "$BASE/logs/api_server_candidate_only_8001_guard.err.log" &
  sleep 5
  if health_ok; then
    log "dashboard restarted successfully"
  else
    log "dashboard restart attempted but health still failing"
  fi
}

if ! health_ok; then
  start_dashboard
fi

if "$PY" "$ROUTE_SCRIPT" >> "$BASE/logs/caddy_dashboard_route_reapply.log" 2>> "$BASE/logs/caddy_dashboard_route_reapply.err.log"; then
  log "caddy route verified"
else
  log "caddy route verification failed"
fi
