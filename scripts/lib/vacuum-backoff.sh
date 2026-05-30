#!/usr/bin/env bash
set -euo pipefail

backoff_seconds() {
  local retry_count="${1:-0}"
  local base="${MIZUCHI_BACKOFF_BASE_SECONDS:-300}"
  local max="${MIZUCHI_BACKOFF_MAX_SECONDS:-3600}"
  local wait=$(( base * (2 ** retry_count) ))
  if (( wait > max )); then
    wait="$max"
  fi
  echo "$wait"
}

is_quota_error() {
  local text="${1:-}"
  grep -Eqi '429|quota|rate limit' <<<"$text"
}
