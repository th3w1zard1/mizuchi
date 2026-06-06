#!/usr/bin/env bash
# Autonomous match loop: scorer → matcher → build-and-verify per pending queue item.
# Verbose trace on stderr; use --quiet to suppress trace (progress.log still updated).
set -euo pipefail

root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
max_attempts="${MIZUCHI_MAX_ATTEMPTS:-10}"
max_infra_retries="${MIZUCHI_MAX_INFRA_RETRIES:-5}"

# shellcheck source=scripts/lib/check-log.sh
source "$root_dir/scripts/lib/check-log.sh"
# shellcheck source=scripts/lib/guide-manifest.sh
source "$root_dir/scripts/lib/guide-manifest.sh"
# shellcheck source=scripts/lib/queue-state.sh
source "$root_dir/scripts/lib/queue-state.sh"
# shellcheck source=scripts/lib/vacuum-backoff.sh
source "$root_dir/scripts/lib/vacuum-backoff.sh"
# shellcheck source=scripts/lib/vacuum-state.sh
source "$root_dir/scripts/lib/vacuum-state.sh"

usage() {
  cat <<EOF
Usage: vacuum.sh [--quiet]

Runs the vacuum match loop until the pending queue is empty.

Options:
  --quiet   Suppress verbose trace (keep vacuum_log progress + summary)
  -h, --help  Show help

Examples:
  ./scripts/init-vacuum-state.sh
  ./scripts/vacuum.sh
  ./scripts/vacuum.sh --quiet

Env:
  MIZUCHI_MAX_ATTEMPTS       Max matcher/verify retries per function (default: 10)
  MIZUCHI_MAX_INFRA_RETRIES  Max infra retries before failed state (default: 5)
  MIZUCHI_STATE_DIR          Queue/scores directory (default: state/)
  MIZUCHI_PROGRESS_LOG       Append-only progress log (default: logs/progress.log)
EOF
}

quiet=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) quiet=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; echo "  ./scripts/vacuum.sh --help" >&2; exit 2 ;;
  esac
done

[[ "$quiet" -eq 1 ]] && check_log_configure --quiet
check_log_init "vacuum"
guide_manifest_load "$root_dir"
guide_manifest_trace_defaults "$root_dir"

log_queue_write() {
  local detail="${1:-queue.json updated}"
  local existed=0
  [[ -f "$queue_file" ]] && existed=1
  check_log_file_written "$queue_file" "$root_dir" "$existed"
  check_log_trace "queue $detail"
}

prompt_target_obj() {
  local prompt_name="${1:?missing prompt name}"
  local path="$GUIDE_PROMPTS_DIR/$prompt_name/target.o"
  if [[ -f "$path" ]]; then
    check_log_read_file "$path" "$(guide_manifest_rel "$root_dir" "$path")" "target.o"
    echo "$path"
    return
  fi
  check_log_trace "read  $(guide_manifest_rel "$root_dir" "$path") (missing target.o)"
  echo ""
}

graceful_shutdown() {
  vacuum_log "shutdown: received signal, state preserved"
  check_log_summary "VACUUM_SHUTDOWN"
  exit 0
}
trap graceful_shutdown INT TERM

queue_init
check_log_read_file "$queue_file" "$(guide_manifest_rel "$root_dir" "$queue_file")" "queue state"

scorer_args=()
[[ "$quiet" -eq 1 ]] && scorer_args=(--quiet)
check_log_run_step "scorer"
if ! "$root_dir/scripts/scorer.sh" "${scorer_args[@]}" >/dev/null; then
  check_log_trace "warn  scorer failed (continuing with existing scores)"
fi

vacuum_log "starting vacuum loop"
check_log_run_step "vacuum loop"

declare -A infra_retries=()

record_infra_retry() {
  local fn="${1:?missing function name}"
  local reason="${2:-infra failure}"
  local count="$(( ${infra_retries[$fn]:-0} + 1 ))"
  infra_retries["$fn"]="$count"
  if (( count >= max_infra_retries )); then
    queue_move "$fn" pending failed
    log_queue_write "move $fn pending→failed (infra exhausted)"
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
  check_log_trace "step  prompt=$next attempt=$((attempts + 1))"

  matcher_args=(--prompt "$next")
  [[ "$quiet" -eq 1 ]] && matcher_args=(--quiet "${matcher_args[@]}")
  tmp_err="$(mktemp)"
  set +e
  matcher_out="$("$root_dir/scripts/matcher.sh" "${matcher_args[@]}" 2>"$tmp_err")"
  matcher_rc=$?
  matcher_err="$(cat "$tmp_err")"
  rm -f "$tmp_err"
  set -e

  if [[ "$matcher_rc" -ne 0 ]]; then
    combined="${matcher_err}${matcher_out}"
    if is_quota_error "$combined"; then
      record_infra_retry "$next" "quota/rate limit detected" || true
      continue
    fi
    if grep -qiE 'Matcher invocation (failed|not configured)' <<<"$combined"; then
      record_infra_retry "$next" "matcher unavailable" || true
      continue
    fi
    count="$(queue_increment_attempt "$next")"
    log_queue_write "increment attempts $next → $count"
    if (( count >= max_attempts )); then
      queue_move "$next" pending difficult
      log_queue_write "move $next pending→difficult"
      vacuum_log "difficult: $next after $count attempts"
    else
      vacuum_log "retry: matcher failed for $next ($count/$max_attempts)"
    fi
    continue
  fi

  target_obj="$(prompt_target_obj "$next")"
  if [[ -z "$target_obj" ]]; then
    infra_count="$(( ${infra_retries[$next]:-0} + 1 ))"
    infra_retries["$next"]="$infra_count"
    if (( infra_count >= max_infra_retries )); then
      queue_move "$next" pending failed
      log_queue_write "move $next pending→failed (no target.o)"
      vacuum_log "failed: $next target.o missing ($infra_count/$max_infra_retries)"
    else
      vacuum_log "infra: target.o missing for $next ($infra_count/$max_infra_retries)"
    fi
    continue
  fi

  verify_args=(--prompt "$next" --target "$target_obj" --commit)
  [[ "$quiet" -eq 1 ]] && verify_args=(--quiet "${verify_args[@]}")
  set +e
  verify_out="$("$root_dir/scripts/build-and-verify.sh" "${verify_args[@]}" 2>/dev/null)"
  verify_rc=$?
  set -e
  verify_status="$(jq -r '.status // empty' <<<"$verify_out" 2>/dev/null || true)"

  if [[ "$verify_rc" -eq 0 && "$verify_status" == "matched" ]]; then
    queue_move "$next" pending matched
    log_queue_write "move $next pending→matched"
    vacuum_log "matched: $next"
    unset 'infra_retries[$next]'
  elif [[ "$verify_status" == "infra_error" ]]; then
    record_infra_retry "$next" "verification unavailable" || true
  else
    unset 'infra_retries[$next]'
    count="$(queue_increment_attempt "$next")"
    log_queue_write "increment attempts $next → $count"
    if (( count >= max_attempts )); then
      queue_move "$next" pending difficult
      log_queue_write "move $next pending→difficult (verify failed)"
      vacuum_log "difficult: $next after $count attempts"
    else
      vacuum_log "retry: $next verify failed ($count/$max_attempts)"
    fi
  fi
done

check_log_summary "VACUUM_OK"
printf 'VACUUM_OK queue=%s\n' "$(guide_manifest_rel "$root_dir" "$queue_file")" >&2
