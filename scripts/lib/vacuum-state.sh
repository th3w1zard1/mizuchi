#!/usr/bin/env bash
set -euo pipefail

vacuum_log() {
  local msg="${1:-}"
  local root_dir="${MIZUCHI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
  local log_dir="${MIZUCHI_LOG_DIR:-$root_dir/logs}"
  local progress_log="${MIZUCHI_PROGRESS_LOG:-$log_dir/progress.log}"
  mkdir -p "$log_dir"
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$msg" | tee -a "$progress_log"
}
