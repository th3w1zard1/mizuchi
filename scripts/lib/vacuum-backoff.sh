#!/usr/bin/env bash
# Quota detection and exponential backoff helpers for the autonomous vacuum loop.
set -euo pipefail

vacuum_is_quota_log() {
  local log_file="$1"
  [[ -f "$log_file" ]] || return 1
  rg -i "quota|rate[ -]?limit|429|too many requests|overloaded|capacity|try again later" "$log_file" >/dev/null 2>&1
}

vacuum_backoff_seconds() {
  local retry_count="$1" base="${2:-300}" max="${3:-3600}"
  local seconds="$base" i
  if [[ "$retry_count" =~ ^[0-9]+$ ]]; then
    for ((i=1; i<retry_count; i++)); do
      seconds=$((seconds * 2))
      if [[ "$seconds" -ge "$max" ]]; then
        seconds="$max"
        break
      fi
    done
  fi
  if [[ "$seconds" -gt "$max" ]]; then
    seconds="$max"
  fi
  printf '%s\n' "$seconds"
}
