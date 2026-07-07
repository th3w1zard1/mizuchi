#!/usr/bin/env bash
# Probe Cloudflare-protected indexers through the hybrid router and trigger maintenance on failure.
set -euo pipefail

STACK_DIR="${STACK_DIR:-/opt/flaresolverr-stack}"
LOG_FILE="${STACK_DIR}/logs/agent-triage.log"
mkdir -p "${STACK_DIR}/logs"

PROBE_URLS=(
  "https://eztvx.to/"
  "https://1337x.to/home/"
)

log() { echo "$(date -Iseconds) $*" | tee -a "$LOG_FILE"; }

probe() {
  local url="$1"
  local payload
  payload=$(jq -n --arg url "$url" --argjson t 90000 '{cmd:"request.get",url:$url,maxTimeout:$t}')
  local out="/tmp/fs-probe-$$.json"
  local start
  start=$(date +%s)
  curl -s --max-time 120 -X POST http://127.0.0.1:8191/v1 \
    -H 'Content-Type: application/json' -d "$payload" > "$out" || true
  local elapsed=$(( $(date +%s) - start ))
  local status msg bytes
  status=$(jq -r '.status // "error"' "$out" 2>/dev/null || echo error)
  msg=$(jq -r '.message // ""' "$out" 2>/dev/null | head -c 120)
  bytes=$(jq -r '.solution.response // ""' "$out" 2>/dev/null | wc -c)
  rm -f "$out"
  if [[ "$status" == "ok" && "$bytes" -gt 5000 ]]; then
    log "PROBE OK ${url} (${elapsed}s, ${bytes} bytes) ${msg}"
    return 0
  fi
  log "PROBE FAIL ${url} (${elapsed}s) status=${status} ${msg}"
  return 1
}

log "=== Triage run ==="
ok=0
fail=0
for u in "${PROBE_URLS[@]}"; do
  if probe "$u"; then ok=$((ok+1)); else fail=$((fail+1)); fi
done

if [[ "$fail" -gt 0 ]]; then
  log "ACTION: run maintain-stack or restart patchright-proxy"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user start flaresolverr-maintain.service 2>/dev/null || true
  fi
  bash "${STACK_DIR}/scripts/maintain-stack.sh" || true
fi

log "Triage done: ${ok} ok, ${fail} fail"
