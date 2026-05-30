#!/usr/bin/env bash
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
max_attempts="${MIZUCHI_MAX_ATTEMPTS:-10}"

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

retry_count=0
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
      wait_secs="$(backoff_seconds "$retry_count")"
      vacuum_log "backoff: quota/rate limit detected; waiting ${wait_secs}s"
      sleep "$wait_secs"
      retry_count=$((retry_count + 1))
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
  if [[ "$verify_rc" -eq 0 && "$(jq -r '.status // empty' <<<"$verify_out" 2>/dev/null)" == "matched" ]]; then
    queue_move "$next" pending matched
    vacuum_log "matched: $next"
    retry_count=0
  else
    count="$(queue_increment_attempt "$next")"
    if (( count >= max_attempts )); then
      queue_move "$next" pending difficult
      vacuum_log "difficult: $next after $count attempts"
    else
      vacuum_log "retry: $next verify failed ($count/$max_attempts)"
    fi
  fi
done
