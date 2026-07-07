#!/usr/bin/env bash
# Health-check the FlareSolverr hybrid stack and restart failed components.
set -euo pipefail

STACK_DIR="${STACK_DIR:-/opt/flaresolverr-stack}"
LOG_FILE="${STACK_DIR}/logs/maintain.log"
mkdir -p "${STACK_DIR}/logs"

log() { echo "$(date -Iseconds) $*" | tee -a "$LOG_FILE"; }

check_endpoint() {
  local url="$1"
  local name="$2"
  local body='{"cmd":"sessions.list"}'
  local code
  code=$(curl -s -o /tmp/fs-health.json -w '%{http_code}' --max-time 15 \
    -X POST "${url}/v1" -H 'Content-Type: application/json' -d "$body" || echo "000")
  if [[ "$code" != "200" ]]; then
    log "FAIL ${name}: HTTP ${code}"
    return 1
  fi
  if ! grep -q '"status":"ok"' /tmp/fs-health.json 2>/dev/null && ! grep -q '"status": "ok"' /tmp/fs-health.json 2>/dev/null; then
    log "FAIL ${name}: bad JSON response"
    return 1
  fi
  log "OK ${name}"
  return 0
}

restart_unit() {
  local unit="$1"
  log "Restarting ${unit}"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user restart "${unit}" 2>/dev/null || systemctl restart "${unit}" 2>/dev/null || true
  elif [[ "$(uname -s)" == "Darwin" ]]; then
    launchctl kickstart -k "gui/$(id -u)/${unit}" 2>/dev/null || true
  fi
  sleep 5
}

if [[ -r /proc/meminfo ]]; then
  mem_avail_kb=$(grep -E '^MemAvailable:' /proc/meminfo | awk '{print $2}')
  if [[ "${mem_avail_kb:-0}" -lt 512000 ]]; then
    log "WARN low memory (${mem_avail_kb} kB available); health-only mode"
  fi
fi

failed=0
check_endpoint "http://127.0.0.1:8193" "flaresolverr-backend" || { restart_unit flaresolverr.service; failed=1; }
check_endpoint "http://127.0.0.1:8192" "patchright-proxy" || { restart_unit patchright-proxy.service; failed=1; }
check_endpoint "http://127.0.0.1:8191" "hybrid-router" || { restart_unit hybrid-router.service; failed=1; }

if [[ "$failed" -eq 1 ]]; then
  sleep 8
  check_endpoint "http://127.0.0.1:8191" "hybrid-router-post-restart" || log "CRITICAL hybrid still down"
fi

find "${STACK_DIR}/patchright-proxy/profiles" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} + 2>/dev/null || true

if [[ -f "$LOG_FILE" ]] && [[ $(wc -l < "$LOG_FILE") -gt 2000 ]]; then
  tail -n 1000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

log "Maintenance cycle complete"
