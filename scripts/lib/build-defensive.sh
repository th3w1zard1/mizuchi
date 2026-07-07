#!/usr/bin/env bash
# Compile wrapper that preserves full logs while emitting capped failure summaries.
set -euo pipefail

build_defensive_usage() {
  cat >&2 <<'EOF'
usage: build-defensive.sh --log <file> --summary <file> [--cwd <dir>] -- <command...>

Runs a build command, writes the full combined output to --log, and writes a
bounded summary to --summary. Exit code is the build command exit code.
EOF
}

build_defensive_summarize() {
  local log_file="$1" summary_file="$2" exit_code="$3" limit_bytes="${4:-5120}"
  local first_error

  mkdir -p "$(dirname "$summary_file")"
  if [[ "$exit_code" -eq 0 ]]; then
    {
      printf 'BUILD SUCCEEDED\n'
      printf 'full_log: %s\n' "$log_file"
    } >"$summary_file"
    return 0
  fi

  first_error="$(grep -im1 -E '(^|[^[:alpha:]])(fatal error|error:|undefined reference|ld:|collect2:|No such file|not found)' "$log_file" || true)"
  {
    printf 'BUILD FAILED\n'
    if [[ -n "$first_error" ]]; then
      printf 'first_error: %s\n' "$first_error"
    else
      printf 'first_error: <none detected>\n'
    fi
    printf 'exit_code: %s\n' "$exit_code"
    printf 'full_log: %s\n' "$log_file"
    printf '\n--- tail last %s bytes ---\n' "$limit_bytes"
    tail -c "$limit_bytes" "$log_file" 2>/dev/null || true
  } >"$summary_file"
}

build_defensive_main() {
  local log_file="" summary_file="" limit_bytes=5120
  local cwd=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --log) log_file="$2"; shift 2 ;;
      --summary) summary_file="$2"; shift 2 ;;
      --limit-bytes) limit_bytes="$2"; shift 2 ;;
      --cwd) cwd="$2"; shift 2 ;;
      --) shift; break ;;
      -h|--help) build_defensive_usage; return 0 ;;
      *) echo "build-defensive: unknown option: $1" >&2; build_defensive_usage; return 2 ;;
    esac
  done

  if [[ -z "$log_file" || -z "$summary_file" || "$#" -eq 0 ]]; then
    build_defensive_usage
    return 2
  fi

  mkdir -p "$(dirname "$log_file")"
  set +e
  if [[ -n "$cwd" ]]; then
    (cd "$cwd" && "$@") >"$log_file" 2>&1
  else
    "$@" >"$log_file" 2>&1
  fi
  local rc=$?
  set -e
  build_defensive_summarize "$log_file" "$summary_file" "$rc" "$limit_bytes"
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  build_defensive_main "$@"
fi
