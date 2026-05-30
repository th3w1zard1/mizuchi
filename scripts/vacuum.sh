#!/usr/bin/env bash
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
max_attempts="${MIZUCHI_MAX_ATTEMPTS:-10}"
max_infra_retries="${MIZUCHI_MAX_INFRA_RETRIES:-5}"

# shellcheck source=scripts/lib/queue-state.sh
source "$root_dir/scripts/lib/queue-state.sh"
# shellcheck source=scripts/lib/vacuum-backoff.sh
source "$root_dir/scripts/lib/vacuum-backoff.sh"
# shellcheck source=scripts/lib/vacuum-state.sh
source "$root_dir/scripts/lib/vacuum-state.sh"

prompt_target_obj() {
  local prompt_name="${1:?missing prompt name}"
  if [[ -f "$root_dir/prompts/$prompt_name/target.o" ]]; then
    echo "$root_dir/prompts/$prompt_name/target.o"
    return
  fi
  echo ""
}

graceful_shutdown() {
  vacuum_log "shutdown: received signal, state preserved"
  exit 0
}
trap graceful_shutdown INT TERM

queue_init
"$root_dir/scripts/scorer.sh" >/dev/null || true
vacuum_log "starting vacuum loop"

declare -A infra_retries=()

record_infra_retry() {
  local fn="${1:?missing function name}"
  local reason="${2:-infra failure}"
  local count="$(( ${infra_retries[$fn]:-0} + 1 ))"
  infra_retries["$fn"]="$count"
  if (( count >= max_infra_retries )); then
    queue_move "$fn" pending failed
    vacuum_log "failed: $fn infra retries exhausted ($count/$max_infra_retries): $reason"
    return 1
  fi
  wait_secs="$(backoff_seconds "$((count - 1))")"
  vacuum_log "infra: $reason for $fn; waiting ${wait_secs}s ($count/$max_infra_retries)"
  sleep "$wait_secs"
  return 0
}

while true; do
  next="$(queue_get_next_pending)"
  if [[ -z "$next" ]]; then
    vacuum_log "queue empty; exiting"
    break
  fi

  attempts="$(queue_get_attempts "$next")"
  vacuum_log "start: $next (attempt $((attempts + 1)))"

  set +e
  matcher_out="$("$root_dir/scripts/matcher.sh" --prompt "$next" 2>&1)"
  matcher_rc=$?
  set -e
  if [[ "$matcher_rc" -ne 0 ]]; then
    if is_quota_error "$matcher_out"; then
      record_infra_retry "$next" "quota/rate limit detected" || true
      continue
    fi
    if grep -qiE 'Matcher invocation (failed|not configured)' <<<"$matcher_out"; then
      record_infra_retry "$next" "matcher unavailable" || true
      continue
    fi
    count="$(queue_increment_attempt "$next")"
    if (( count >= max_attempts )); then
      queue_move "$next" pending difficult
      vacuum_log "difficult: $next after $count attempts"
    else
      vacuum_log "retry: matcher failed for $next ($count/$max_attempts)"
    fi
    continue
  fi

  target_obj="$(prompt_target_obj "$next")"
  if [[ -z "$target_obj" ]]; then
    count="$(queue_increment_attempt "$next")"
    if (( count >= max_attempts )); then
      queue_move "$next" pending difficult
      vacuum_log "difficult: $next target.o missing"
    else
      vacuum_log "retry: $next target.o missing"
    fi
    continue
  fi

  set +e
  verify_out="$("$root_dir/scripts/build-and-verify.sh" --prompt "$next" --target "$target_obj" --commit 2>&1)"
  verify_rc=$?
  set -e
  verify_status="$(jq -r '.status // empty' <<<"$verify_out" 2>/dev/null || true)"
  if [[ "$verify_rc" -eq 0 && "$verify_status" == "matched" ]]; then
    queue_move "$next" pending matched
    vacuum_log "matched: $next"
    unset 'infra_retries[$next]'
  elif [[ "$verify_status" == "infra_error" ]]; then
    record_infra_retry "$next" "verification unavailable" || true
  else
    unset 'infra_retries[$next]'
    count="$(queue_increment_attempt "$next")"
    if (( count >= max_attempts )); then
      queue_move "$next" pending difficult
      vacuum_log "difficult: $next after $count attempts"
    else
      vacuum_log "retry: $next verify failed ($count/$max_attempts)"
    fi
  fi
done
