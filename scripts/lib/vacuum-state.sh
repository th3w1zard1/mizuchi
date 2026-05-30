#!/usr/bin/env bash
set -euo pipefail

vacuum_log() {
  local msg="${1:-}"
  local root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  local log_dir="${MIZUCHI_LOG_DIR:-$root_dir/logs}"
  local progress_log="${MIZUCHI_PROGRESS_LOG:-$log_dir/progress.log}"
  local existed=0

  mkdir -p "$log_dir"
  [[ -f "$progress_log" ]] && existed=1

  if declare -F check_log_trace >/dev/null 2>&1; then
    check_log_trace "$msg"
  else
    printf 'vacuum: %s\n' "$msg" >&2
  fi

  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" >>"$progress_log"

  if declare -F check_log_file_appended >/dev/null 2>&1 && [[ "$existed" -eq 1 ]]; then
    check_log_file_appended "$progress_log" "$root_dir" "vacuum progress"
  elif declare -F check_log_file_written >/dev/null 2>&1; then
    check_log_file_written "$progress_log" "$root_dir" "$existed"
  fi
}
